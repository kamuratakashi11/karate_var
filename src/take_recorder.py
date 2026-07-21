"""
テイクモード(型の演武など、開始〜終了を通しで録画するモード)の
クリップ抽出モジュール。

clip_extractor.py と同じ「バッファのTSセグメント群をconcatしてtrimする」
パターンを踏襲するが、固定のCLIP_DURATION_SECONDS秒ではなく、
start_take()〜stop_take()の実経過時間ぶんを可変長で切り出す点が異なる。

テイクはFIFOで自動削除される clips/ とは別枠の data/takes/ に置き、
保護期間の概念も持たない(削除はWeb画面の一括削除UIでのみ行う)。
"""

import glob
import hashlib
import itertools
import json
import os
import subprocess
import threading
import time

from config import (
    BUFFER_DIR, TAKE_DIR, TAKE_INDEX_PATH, SEGMENT_SECONDS, COURT_NAME,
)
from shared_lock import buffer_lock

_take_id_counter = itertools.count()
_index_lock = threading.Lock()


def _ascii_court_slug(text):
    """saved_clips.py と同じ考え方: ファイル名(=URLパスの一部)には
    日本語を含みうるCOURT_NAMEをそのまま使わず、ASCII専用のハッシュにする。
    人が読める表記はindex.jsonl側にそのまま保持する。"""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


class TakeRecorder:
    def __init__(self):
        self._start_time = None

    def start_take(self):
        """テイク開始(F2スタート、テイクモード時)に呼ぶ。開始時刻を記録するだけ。
        バッファのcleanup一時停止はmain.py側でrecorder.pause_cleanup()を呼んで行う。"""
        self._start_time = time.time()

    def is_in_progress(self):
        return self._start_time is not None

    def stop_take(self):
        """
        テイク終了(F2ストップ、または最大時間キャップ)に呼ぶ。
        start_take()からの経過時間ぶんをリングバッファから切り出し、
        data/takes/ にmp4として保存する。

        戻り値: 保存したテイクのメタ情報(dict)
        """
        if self._start_time is None:
            raise RuntimeError("start_take()が呼ばれていません")

        elapsed = max(0.1, time.time() - self._start_time)
        self._start_time = None

        take_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}_{next(_take_id_counter)}"
        concat_path = os.path.join(TAKE_DIR, f"_concat_{take_id}.txt")
        combined_path = os.path.join(TAKE_DIR, f"_combined_{take_id}.ts")
        court_part = _ascii_court_slug(COURT_NAME)
        final_path = os.path.join(TAKE_DIR, f"take_{court_part}_{take_id}.mp4")

        with buffer_lock:
            segments = sorted(glob.glob(os.path.join(BUFFER_DIR, "seg_*.ts")))
            if not segments:
                raise RuntimeError("バッファにセグメントがありません(録画が開始されていない可能性)")

            # 経過時間をカバーするのに必要な個数(安全マージンとして+2)。
            # cleanup_loopはstart_take()時点からpause_cleanup()で止まっている前提なので、
            # 区間全体のセグメントがまだ残っているはず。
            needed = int(elapsed // SEGMENT_SECONDS) + 2
            recent = segments[-needed:]

            if not recent:
                raise RuntimeError("直近のセグメントが見つかりません")

            with open(concat_path, "w") as f:
                for seg in recent:
                    f.write(f"file '{os.path.abspath(seg)}'\n")

            subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path,
                 "-c", "copy", combined_path],
                check=True, stderr=subprocess.DEVNULL,
            )

        duration = self._probe_duration(combined_path)
        start = max(0.0, duration - elapsed)

        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", combined_path,
             "-t", f"{elapsed:.3f}", "-c", "copy",
             "-movflags", "+faststart", final_path],
            check=True, stderr=subprocess.DEVNULL,
        )

        os.remove(concat_path)
        os.remove(combined_path)

        entry = self._register_take(final_path, elapsed)
        return entry

    def _register_take(self, path, duration_sec):
        taken_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        entry = {
            "taken_at": taken_at,
            "taken_at_epoch": time.time(),
            "court": COURT_NAME,
            "filename": os.path.basename(path),
            "duration_sec": round(duration_sec, 2),
        }
        with _index_lock:
            with open(TAKE_INDEX_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    @staticmethod
    def _probe_duration(path):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True,
        )
        return float(result.stdout.strip())


def list_takes():
    """保存済みテイクの一覧を新しい順で返す"""
    if not os.path.exists(TAKE_INDEX_PATH):
        return []
    with open(TAKE_INDEX_PATH, encoding="utf-8") as f:
        lines = f.readlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries = [e for e in entries if os.path.exists(os.path.join(TAKE_DIR, e["filename"]))]
    entries.reverse()
    return entries


def delete_takes(filenames):
    """
    指定したテイクをdata/takes/とindex.jsonlの両方から削除する。
    filenames: ファイル名のリスト(パスは含まない想定。basename化してから扱う)
    """
    targets = {os.path.basename(f) for f in filenames}

    with _index_lock:
        for name in targets:
            path = os.path.join(TAKE_DIR, name)
            if os.path.exists(path):
                os.remove(path)

        if not os.path.exists(TAKE_INDEX_PATH):
            return
        with open(TAKE_INDEX_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        remaining = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if entry.get("filename") not in targets:
                remaining.append(json.dumps(entry, ensure_ascii=False))
        with open(TAKE_INDEX_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(remaining) + ("\n" if remaining else ""))
