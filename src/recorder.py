"""
リングバッファ録画モジュール。

VideoSource(実カメラ or Mock)からフレームを取得し、FFmpegの
segmentマルチプレクサに生フレームをパイプで流し込むことで、
SEGMENT_SECONDS 秒ごとの小さい映像断片を連続生成する。
BUFFER_SEGMENTS 個を超えた古いセグメントは自動的に削除し、
常に直近 SEGMENT_SECONDS * BUFFER_SEGMENTS 秒分だけを
ディスク上に保持する(=容量が増え続けない設計)。

断片形式はMPEG-TS(.ts)を採用している(mp4ではない)。
理由: mp4は「書き込みが完了してファイルの末尾にインデックス情報(moov atom)
が書かれるまで正しく読めない」形式のため、業者のタイマーとの同期精度を
上げるには「今まさに書き込み中の断片」を除外せざるを得ず、
最大でSEGMENT_SECONDS秒分のズレが発生してしまう(F2キー押下時のタイマー
停止と、映像の切り出し内容がズレる)。TS形式は書き込み中でも安全に
読めるため、この断片も含めて切り出せる。=タイマーとの同期精度が
大幅に向上する(ズレはほぼ1フレーム=約16.7ms程度まで縮小する)。

「やめ」操作時は clip_extractor.py がこのセグメント群から
直近6秒を切り出し、最終的にmp4へ変換して保存する。
"""

import glob
import os
import subprocess
import threading
import time

from config import (
    FRAME_WIDTH, FRAME_HEIGHT, FPS,
    SEGMENT_SECONDS, BUFFER_SEGMENTS, BUFFER_DIR,
    FFMPEG_PRESET, FFMPEG_CRF, HEALTH_STALE_THRESHOLD_SEC,
)
from shared_lock import buffer_lock


class SegmentRingBufferRecorder:
    def __init__(self, source, on_warning=None):
        """
        source: VideoSource (RealCameraSource または MockCameraSource)
        on_warning: 映像取得に失敗した際に呼ばれるコールバック(GUI警告表示用)
        """
        self.source = source
        self.on_warning = on_warning
        self._ffmpeg_proc = None
        self._capture_thread = None
        self._cleanup_thread = None
        self._running = False
        self._start_time = None
        self._last_frame_time = None
        self._frame_count = 0

    def get_health(self):
        """中央監視ダッシュボード向けの死活情報を返す"""
        now = time.time()
        last_frame_age = (now - self._last_frame_time) if self._last_frame_time else None
        uptime = (now - self._start_time) if self._start_time else 0
        recording_ok = last_frame_age is not None and last_frame_age < HEALTH_STALE_THRESHOLD_SEC
        return {
            "recording_ok": recording_ok,
            "last_frame_age_sec": round(last_frame_age, 2) if last_frame_age is not None else None,
            "uptime_sec": round(uptime, 1),
            "frame_count": self._frame_count,
        }

    def start(self):
        self.source.start()

        segment_pattern = os.path.join(BUFFER_DIR, "seg_%Y%m%d_%H%M%S.ts")

        # rawvideo(bgr24)を標準入力から受け取り、H.264 + MPEG-TSでセグメント分割保存する。
        # TS形式は書き込み中のファイルでも安全に読めるため、
        # 「今書いている最中の断片」も切り出し対象に含められる(同期精度向上のため)。
        cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", f"{FRAME_WIDTH}x{FRAME_HEIGHT}",
            "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264",
            "-preset", FFMPEG_PRESET,
            "-crf", FFMPEG_CRF,
            "-pix_fmt", "yuv420p",
            "-g", str(FPS),  # 1秒に1回キーフレーム→セグメント境界を綺麗にする
            "-f", "segment",
            "-segment_time", str(SEGMENT_SECONDS),
            "-segment_format", "mpegts",
            "-reset_timestamps", "1",
            "-strftime", "1",
            segment_pattern,
        ]

        self._ffmpeg_proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )

        self._running = True
        self._start_time = time.time()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()

        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _capture_loop(self):
        fail_count = 0
        while self._running:
            ok, frame = self.source.read()
            if not ok or frame is None:
                fail_count += 1
                if fail_count >= 5 and self.on_warning:
                    self.on_warning("カメラ映像を取得できません。接続を確認してください。")
                time.sleep(0.1)
                continue
            fail_count = 0
            self._last_frame_time = time.time()
            self._frame_count += 1
            try:
                self._ffmpeg_proc.stdin.write(frame.tobytes())
            except (BrokenPipeError, ValueError):
                if self.on_warning:
                    self.on_warning("録画プロセスが停止しました。再起動してください。")
                self._running = False
                break

    def _cleanup_loop(self):
        """BUFFER_SEGMENTS個を超えた古いセグメントファイルを削除し続ける"""
        while self._running:
            # 切り出し処理(clip_extractor.py)が同時に読み込み中でないことを保証してから削除する
            with buffer_lock:
                segments = sorted(glob.glob(os.path.join(BUFFER_DIR, "seg_*.ts")))
                # 末尾(最新)は書き込み中の可能性があるので削除対象から除外する
                deletable = segments[:-1]
                excess = len(deletable) - BUFFER_SEGMENTS
                if excess > 0:
                    for path in deletable[:excess]:
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            time.sleep(SEGMENT_SECONDS)

    def stop(self):
        # 先にcapture_loopを止めてから(=書き込みをやめてから)stdinを閉じる。
        # 順序を逆にすると、閉じた直後にcapture_loopが書き込みを試みて
        # 誤ってon_warningが発火することがあるため。
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=2)
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.stdin.close()
            except Exception:
                pass
            self._ffmpeg_proc.wait(timeout=5)
        self.source.release()
