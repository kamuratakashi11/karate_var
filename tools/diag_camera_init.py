"""
カメラ初期化の「どのステップに何秒かかっているか」を実測する診断スクリプト。

背景: 新しいノートPCで、カメラを開くだけ(list_cameras.py)は速いのに、
録画本体(RealCameraSource)の起動には約5分かかる事象が見つかった。
両者の違いは cap.set() による FOURCC(MJPG)/解像度/FPS の設定の有無
なので、これらの各 set() 呼び出しの所要時間を1つずつ計測して、
どれがボトルネックかを特定する(ラウンド5の diag_camera.py と同じ手法)。

さらに、遅い場合の回避策の候補として、以下も比較計測する:
  A. 現行方式: CAP_MSMF + FOURCC(MJPG) を最初に設定
  B. CAP_DSHOW バックエンド(fpsは落ちるが初期化が速い可能性)

使い方:
  python tools/diag_camera_init.py --camera 1
"""

import argparse
import os
import time

# MSMFの初期化高速化(camera_source.pyと同じ。cv2 import前に設定する)
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")

import cv2

from_conf_w, from_conf_h, from_conf_fps = 1280, 720, 60


def timed(label, fn):
    t0 = time.monotonic()
    result = fn()
    dt = time.monotonic() - t0
    print(f"  [{dt:6.2f}秒] {label} -> {result}")
    return result


def try_backend(camera_index, backend_name, backend_flag, set_fourcc):
    print(f"\n=== 方式: {backend_name} (FOURCC設定={'あり' if set_fourcc else 'なし'}) ===")
    total0 = time.monotonic()

    cap = timed("VideoCapture(open)",
                lambda: cv2.VideoCapture(camera_index, backend_flag))
    if not cap.isOpened():
        print("  → 開けませんでした")
        cap.release()
        return

    if set_fourcc:
        timed("set FOURCC(MJPG)",
              lambda: cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG")))
    timed("set WIDTH",
          lambda: cap.set(cv2.CAP_PROP_FRAME_WIDTH, from_conf_w))
    timed("set HEIGHT",
          lambda: cap.set(cv2.CAP_PROP_FRAME_HEIGHT, from_conf_h))
    timed("set FPS",
          lambda: cap.set(cv2.CAP_PROP_FPS, from_conf_fps))

    # 実際に1枚読めるまでの時間も計測
    timed("最初の read()", lambda: cap.read()[0])

    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"  実際の設定値: {w}x{h} @ {fps}fps")
    print(f"  === この方式の合計: {time.monotonic() - total0:.2f}秒 ===")
    cap.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args()

    # A. 現行方式(MSMF + FOURCC先出し)
    try_backend(args.camera, "CAP_MSMF + FOURCC", cv2.CAP_MSMF, set_fourcc=True)

    # B. MSMFでFOURCCを設定しない(どのsetが重いかの切り分け)
    try_backend(args.camera, "CAP_MSMF (FOURCCなし)", cv2.CAP_MSMF, set_fourcc=False)

    # C. DSHOWバックエンド(初期化が速い可能性。ただしfpsは落ちうる)
    try_backend(args.camera, "CAP_DSHOW + FOURCC", cv2.CAP_DSHOW, set_fourcc=True)

    print("\n各方式の『合計』と、どのステップ(特にset FOURCC/最初のread)に"
          "時間がかかっているかを比較してください。")


if __name__ == "__main__":
    main()
