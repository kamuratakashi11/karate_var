"""
映像ソースの抽象化。

RealCameraSource: OpenCV経由でUSBカメラ(UVC)から取得。実機到着後はこちらを使う。
MockCameraSource : カメラなしでパイプライン全体(録画バッファ・6秒切り出し等)を
                   検証するための疑似映像ジェネレータ。
                   フレームに「経過時間」と「フレーム番号」を焼き込むため、
                   後で切り出したクリップが本当に直前6秒分になっているか
                   目視・自動の両方で検証できる。
"""

import os

# OpenCVのMSMF(Media Foundation)バックエンドは、初期化時にハードウェアの
# 変換器(MFT)を列挙する処理があり、一部のPCではこれが極端に遅くなる
# (実機で、VideoCapture openと各cap.set()にそれぞれ約66秒、合計約4.5分
# かかる事象を確認した)。この環境変数でHW変換器の列挙を無効化すると
# 初期化が大幅に高速化する。必ずcv2をimportする前に設定する必要がある。
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import time
import cv2
import numpy as np
from abc import ABC, abstractmethod

from config import FRAME_WIDTH, FRAME_HEIGHT, FPS, CAMERA_BACKEND


class VideoSource(ABC):
    @abstractmethod
    def start(self):
        ...

    @abstractmethod
    def read(self):
        """1フレーム取得。 (成功フラグ, BGRのndarray) を返す"""
        ...

    @abstractmethod
    def release(self):
        ...


class RealCameraSource(VideoSource):
    """市販USBカメラ(UVC対応)からの取得。カメラ到着後はこれをmain.pyで使う"""

    def __init__(self, device_index=0):
        self.device_index = device_index
        self.cap = None

    def start(self):
        # バックエンドはPCによって最適な方が異なるためconfig.pyで切替可能に
        # している(CAMERA_BACKEND)。
        # ・あるPC: CAP_DSHOWだと60fps要求時に実質25〜30fpsしか出ず、CAP_MSMFが
        #   必要だった。
        # ・別のノートPC: CAP_MSMFは1操作あたり約66秒(合計約4.5分)かかり
        #   起動が実用にならず、CAP_DSHOWなら約3秒で起動できた。
        #
        # FOURCC(MJPG)を設定する順序がバックエンドで逆になる点に注意
        # (実機のtools/diag_dshow_modes.pyで確認):
        # ・MSMF: FOURCCを解像度/fpsより「先」に設定しないとMJPGにならない。
        # ・DSHOW: FOURCCを解像度/fps設定の「後」に設定しないとMJPGにならず、
        #   YUY2非圧縮(720pではUSB帯域限界で約10fps)にフォールバックする。
        # MJPGにならないと720p60は帯域的に出せないため、この順序は重要。
        backend = self._backend_flag()
        self.cap = cv2.VideoCapture(self.device_index, backend)
        mjpg = cv2.VideoWriter_fourcc(*"MJPG")

        if backend == cv2.CAP_DSHOW:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, FPS)
            self.cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        else:
            self.cap.set(cv2.CAP_PROP_FOURCC, mjpg)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
            self.cap.set(cv2.CAP_PROP_FPS, FPS)

        if not self.cap.isOpened():
            raise RuntimeError(f"カメラ(index={self.device_index})を開けませんでした")

        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        actual_fps = self.cap.get(cv2.CAP_PROP_FPS)
        print(f"[RealCameraSource] 実際の設定値: {actual_w}x{actual_h} @ {actual_fps}fps")

    @staticmethod
    def _backend_flag():
        """config.CAMERA_BACKENDに応じたOpenCVのバックエンド定数を返す。
        Windows以外では常に既定(0)。"""
        if not _is_windows():
            return 0
        backend = (CAMERA_BACKEND or "msmf").strip().lower()
        if backend == "dshow":
            return cv2.CAP_DSHOW
        return cv2.CAP_MSMF

    def read(self):
        ok, frame = self.cap.read()
        return ok, frame

    def release(self):
        if self.cap is not None:
            self.cap.release()


class MockCameraSource(VideoSource):
    """
    カメラ未到着時のダミー映像ソース。
    ・背景に経過時間(秒)とフレーム番号を大きく描画
    ・円が左右に動く(動き判定・コマ送り確認用)
    ・一定間隔で画面全体が赤く点滅する「イベント」を発生させ、
      「この瞬間が6秒クリップにちゃんと含まれているか」を目視確認できるようにする
    """

    def __init__(self, width=FRAME_WIDTH, height=FRAME_HEIGHT, fps=FPS, event_interval_sec=5.0):
        self.width = width
        self.height = height
        self.fps = fps
        self.event_interval_sec = event_interval_sec
        self._start_time = None
        self._frame_count = 0

    def start(self):
        self._start_time = time.monotonic()
        self._frame_count = 0

    def read(self):
        elapsed = time.monotonic() - self._start_time
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # 「イベント」判定: event_interval_sec ごとに0.3秒だけ画面を赤くする
        # (審判の「やめ」＝技が決まった瞬間を模擬)
        is_event = (elapsed % self.event_interval_sec) < 0.3
        if is_event:
            frame[:, :] = (0, 0, 200)  # 赤(BGR)
            cv2.putText(frame, "EVENT!", (self.width // 2 - 150, self.height // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)

        # 動く円(コマ送り確認用の目印)
        cx = int((np.sin(elapsed) * 0.4 + 0.5) * self.width)
        cy = self.height // 2
        cv2.circle(frame, (cx, cy), 40, (0, 255, 0), -1)

        # 経過時間とフレーム番号を焼き込み(検証用)
        cv2.putText(frame, f"t={elapsed:6.2f}s  frame={self._frame_count}",
                    (30, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

        self._frame_count += 1

        # 実フレームレートに合わせてウェイト(呼び出し側のループ速度に依存させないため)
        target_time = self._start_time + self._frame_count / self.fps
        sleep_time = target_time - time.monotonic()
        if sleep_time > 0:
            time.sleep(sleep_time)

        return True, frame

    def release(self):
        pass


def _is_windows():
    import platform
    return platform.system() == "Windows"
