"""
「やめ」(記録員によるタイマー停止)操作を受けて、リングバッファ上の
セグメント群から直近 CLIP_DURATION_SECONDS 秒を切り出し、
確定クリップとして保持するモジュール。

保持ルール: 通常はCLIP_SLOTS枠のFIFO(古いものから上書き)。ただし、
作成されたクリップは、タイマーが実際に「動作中」だった累積時間が
CLIP_PROTECTION_RUNNING_SECONDS秒に達するまでは上書き削除の対象にしない
(オフィシャルミス等でストップボタンが連打されても、保護期間中の
クリップは消えず、一時的にCLIP_SLOTSを超えて保持される。タイマーが
十分な時間動いたら、保護が外れて通常のFIFOに戻る)。

タイマー(F2キー)との同期精度を上げるため、「今まさに書き込み中の
最新セグメント」も切り出し対象に含める(TS形式は書き込み中でも
安全に読めるため)。これにより、F2キー押下の瞬間と切り出される
映像の終端のズレは、最大でも1フレーム分程度(60fpsならおよそ16.7ms)
まで縮小される。
"""

import glob
import itertools
import os
import subprocess
import time

from config import (
    BUFFER_DIR, CLIPS_DIR, CLIP_DURATION_SECONDS, CLIP_SLOTS,
    SEGMENT_SECONDS, CLIP_PROTECTION_RUNNING_SECONDS,
)
from shared_lock import buffer_lock

# 短時間に連続して「やめ」が発火した場合でも、clip_idが重複してファイルが
# 上書きされることがないよう、時刻(ミリ秒まで)に加えて連番も付与する。
_clip_id_counter = itertools.count()


class ClipExtractor:
    def __init__(self, running_counter_fn=None):
        """
        running_counter_fn: タイマーの累積動作秒数を返す関数
                             (TimerSyncedKeyListener.get_running_accumulator)。
                             Noneの場合は保護機能を使わず、常に単純なFIFOで動作する
                             (--input-mode enter 使用時などタイマー追跡が無い場合)。
        """
        self.running_counter_fn = running_counter_fn
        # 古い順に並んだリスト。通常はCLIP_SLOTS件だが、保護中のクリップが
        # あると一時的にそれを超えることがある。
        # 各要素: {"path": str, "created_counter": float or None}
        self._slots = []

    def extract_on_yame(self):
        """
        「やめ」操作が呼ばれた瞬間に実行する。
        直近 CLIP_DURATION_SECONDS 秒をカバーするのに十分なセグメントを集め、
        結合してからトリミングし、6秒ちょうどのクリップを作る。
        """
        # 秒単位のタイムスタンプだけだと、短時間に連続して「やめ」が発火した
        # 場合(オフィシャルミスの連打など、まさに保護機能が必要な場面)に
        # clip_idが重複し、後のクリップが前のクリップのファイルを上書きして
        # しまう。ミリ秒+単調増加の連番を付けて確実に一意にする。
        clip_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}_{next(_clip_id_counter)}"
        concat_path = os.path.join(CLIPS_DIR, f"_concat_{clip_id}.txt")
        combined_path = os.path.join(CLIPS_DIR, f"_combined_{clip_id}.ts")
        final_path = os.path.join(CLIPS_DIR, f"bar_clip_{clip_id}.mp4")

        # ここから「セグメント一覧の取得→結合」までは、recorder.py側の
        # クリーンアップ処理(古いセグメント削除)と同時に走ると、
        # 使うはずだったファイルが削除された直後で読めない、という
        # 競合が起き得る。ロックで確実に排他する。
        with buffer_lock:
            segments = sorted(glob.glob(os.path.join(BUFFER_DIR, "seg_*.ts")))
            if not segments:
                raise RuntimeError("バッファにセグメントがありません(録画が開始されていない可能性)")

            # 直近6秒をカバーするのに必要な個数(安全マージンとして+2)。
            # TS形式のため、書き込み中の最新セグメントも除外せず含める
            # (=タイマーとの同期精度を優先する設計)。
            needed = int(CLIP_DURATION_SECONDS // SEGMENT_SECONDS) + 2
            recent = segments[-needed:]

            if not recent:
                raise RuntimeError("直近のセグメントが見つかりません")

            # concat用リストファイルを作成
            with open(concat_path, "w") as f:
                for seg in recent:
                    f.write(f"file '{os.path.abspath(seg)}'\n")

            # 再エンコードなしで結合(高速・劣化なし)。
            # 最新セグメントが書き込み中でも、TS形式なのでその時点までの
            # 内容を安全に読み取れる。実際にファイルを読み込むのはこの
            # subprocess.run自体なので、ここまでロックの中に含める必要がある
            # (concat_pathにファイル一覧を書いただけではまだ読み込んでいない)。
            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
                 "-c", "copy", combined_path],
                check=True, stderr=subprocess.DEVNULL,
            )
            # ここから先はcombined_pathという独立したファイルだけを使うので、
            # 元のセグメントファイル群には依存しない。ロックはここで解放してよい。

        # 結合後の全体長を取得し、末尾から6秒だけを切り出す。
        # 同時にTS→MP4へのコンテナ変換も行う(再生互換性のため、
        # 映像・音声ストリーム自体は再エンコードしない)。
        duration = self._probe_duration(combined_path)
        start = max(0.0, duration - CLIP_DURATION_SECONDS)

        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", combined_path,
             "-t", f"{CLIP_DURATION_SECONDS:.3f}", "-c", "copy",
             "-movflags", "+faststart", final_path],
            check=True, stderr=subprocess.DEVNULL,
        )

        os.remove(concat_path)
        os.remove(combined_path)

        self._register_clip(final_path)
        return final_path

    def _register_clip(self, path):
        """
        新しいクリップを登録し、保護期間を過ぎた古いクリップだけを
        CLIP_SLOTS件に収まるまで削除する(保護中のものは残す=一時的に
        CLIP_SLOTSを超えることを許容する)。
        """
        created_counter = self.running_counter_fn() if self.running_counter_fn else None
        self._slots.append({"path": path, "created_counter": created_counter})
        self._trim()

    def _trim(self):
        while len(self._slots) > CLIP_SLOTS:
            oldest = self._slots[0]
            if not self._is_evictable(oldest):
                # 一番古いものがまだ保護期間中 = これ以上削れない。
                # (一時的にCLIP_SLOTSを超えて保持することを許容する)
                break
            self._slots.pop(0)
            if os.path.exists(oldest["path"]):
                os.remove(oldest["path"])

    def _is_evictable(self, slot):
        """保護期間(CLIP_PROTECTION_RUNNING_SECONDS)を過ぎているかどうか"""
        if self.running_counter_fn is None or slot["created_counter"] is None:
            return True  # タイマー追跡がない場合は保護せず、常に単純なFIFOとして扱う
        elapsed_running = self.running_counter_fn() - slot["created_counter"]
        return elapsed_running >= CLIP_PROTECTION_RUNNING_SECONDS

    def list_current_clips(self):
        """監査画面に表示する現在保持中のクリップ一覧(古い順)"""
        return [s["path"] for s in self._slots]

    @staticmethod
    def _probe_duration(path):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())
