"""
CAP_DSHOWで、実際にMJPG圧縮の60fpsを引き出せる設定順序を探す診断。

背景: 新PCでCAP_MSMFは起動が約4.5分と実用にならず、CAP_DSHOWは起動が
速い(約3秒)が、実測fpsが約10fpsしか出ない事象が見つかった。
10fpsは720p非圧縮(YUY2)のUSB帯域限界に一致するため、DSHOWで
FOURCC(MJPG)がset()でTrueを返しても実際には適用されず、非圧縮モードに
なっていると推測される(cv2.get()の返り値は要求値であり実配信を保証しない。
ラウンド1参照)。

いくつかの設定順序・方法を試し、それぞれについて
「実際に配信されているFOURCC」と「実測fps(read()を60回呼んで計測)」を
出力して、MJPGで60fpsを引き出せる組み合わせがあるか探す。

使い方:
  python tools/diag_dshow_modes.py --camera 1
"""

import argparse
import time

import cv2

W, H, FPS = 1280, 720, 60


def fourcc_to_str(v):
    v = int(v)
    return "".join([chr((v >> (8 * i)) & 0xFF) for i in range(4)])


def measure(cap, frames=60):
    # 最初の数枚は捨てる(初期化直後の不安定分)
    for _ in range(5):
        cap.read()
    t0 = time.monotonic()
    n = 0
    for _ in range(frames):
        ok, _f = cap.read()
        if ok:
            n += 1
    dt = time.monotonic() - t0
    return (n / dt) if dt > 0 else 0.0


def run(camera_index, label, setup):
    print(f"\n=== {label} ===")
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("  開けませんでした")
        cap.release()
        return
    setup(cap)
    actual_fourcc = fourcc_to_str(cap.get(cv2.CAP_PROP_FOURCC))
    w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    req_fps = cap.get(cv2.CAP_PROP_FPS)
    fps = measure(cap)
    print(f"  実配信FOURCC={actual_fourcc}  解像度={int(w)}x{int(h)}  "
          f"要求fps(get)={req_fps}  実測fps={fps:.1f}")
    cap.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camera", type=int, default=0)
    args = parser.parse_args()

    mjpg = cv2.VideoWriter_fourcc(*"MJPG")

    def s_fourcc_first(cap):
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FPS, FPS)

    def s_size_first(cap):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FPS, FPS)

    def s_fourcc_fps_size(cap):
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FPS, FPS)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)

    def s_size_fps_fourcc(cap):
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FPS, FPS)
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)

    def s_fourcc_twice(cap):
        # 1枚読んでから再度FOURCCを設定する(初期化後でないと効かないカメラ対策)
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, H)
        cap.set(cv2.CAP_PROP_FPS, FPS)
        cap.read()
        cap.set(cv2.CAP_PROP_FOURCC, mjpg)

    run(args.camera, "順序1: FOURCC→W→H→FPS(現行と同じ)", s_fourcc_first)
    run(args.camera, "順序2: W→H→FOURCC→FPS", s_size_first)
    run(args.camera, "順序3: FOURCC→FPS→W→H", s_fourcc_fps_size)
    run(args.camera, "順序4: W→H→FPS→FOURCC", s_size_fps_fourcc)
    run(args.camera, "順序5: FOURCC→W→H→FPS→read→FOURCC再設定", s_fourcc_twice)

    print("\n実配信FOURCCが 'MJPG' になっていて実測fpsが60付近の順序があれば、"
          "その順序をcamera_source.pyに採用できます。"
          "どの順序でもMJPGにならない/60fps出ない場合は、DSHOWでは"
          "このカメラの60fpsを引き出せないため、MSMF(起動は遅い)に"
          "戻すことになります。")


if __name__ == "__main__":
    main()
