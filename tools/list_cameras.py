"""
接続されているカメラのデバイス番号を特定するための診断スクリプト。

PCには内蔵カメラ(ノートPCのWebカメラ等)と外付けUSBカメラが同時に
認識されていることが多く、「--camera 0」が必ずしも目的の外付けカメラとは
限らない(内蔵カメラがindex 0を占有していることがある)。ランプの点灯だけを
頼りに判断すると間違えることがあるため、各indexを実際に開いて1枚ずつ
画像を保存し、目視で確認できるようにする。

使い方:
  python tools/list_cameras.py
  python tools/list_cameras.py --max-index 5   (デフォルトは0〜4を試す)

保存先: tools/camera_snapshots/camera_<番号>.jpg
"""

import argparse
import os

import cv2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-index", type=int, default=5,
                         help="0からこの数-1まで試す(デフォルト5→0,1,2,3,4を試す)")
    args = parser.parse_args()

    out_dir = os.path.join(os.path.dirname(__file__), "camera_snapshots")
    os.makedirs(out_dir, exist_ok=True)

    found_any = False
    for index in range(args.max_index):
        print(f"--- camera index {index} を試しています ---")
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF if os.name == "nt" else 0)
        if not cap.isOpened():
            print(f"  index {index}: 開けませんでした(このカメラは存在しないか使用中です)")
            cap.release()
            continue

        # 起動直後は真っ黒/古いフレームのことがあるため、数フレーム読み捨ててから保存する
        ok = False
        frame = None
        for _ in range(10):
            ok, frame = cap.read()

        w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        cap.release()

        if not ok or frame is None:
            print(f"  index {index}: 開けましたが画像を取得できませんでした")
            continue

        found_any = True
        out_path = os.path.join(out_dir, f"camera_{index}.jpg")
        cv2.imwrite(out_path, frame)
        print(f"  index {index}: 画像を保存しました → {out_path} "
              f"(解像度 {int(w)}x{int(h)})")

    if not found_any:
        print("\nどのindexでも画像を取得できませんでした。"
              "カメラのUSB接続や、他のアプリ(Zoom等)がカメラを使用中でないか確認してください。")
    else:
        print(f"\n{out_dir} フォルダの画像を開いて、目的の外付けカメラの映像が"
              "写っているindex番号を確認してください。")


if __name__ == "__main__":
    main()
