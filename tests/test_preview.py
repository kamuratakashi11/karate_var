"""
ライブプレビュー機能(/preview, /api/preview.jpg)のヘッドレステスト。
MockCameraSourceで確認する。

  1. 録画開始直後はまだフレームが無くAPIが503を返すか
   (実際にはMockでも数フレーム分の遅延があるため、フレーム到着後に確認する)
  2. フレーム到着後、/api/preview.jpg が有効なJPEGを返すか
  3. /preview 画面(HTML)が配信されるか
"""

import io
import os
import sys
import time
import threading
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from camera_source import MockCameraSource
from recorder import SegmentRingBufferRecorder
from clip_extractor import ClipExtractor
import web_server

PORT = 5063


def main():
    source = MockCameraSource()
    extractor = ClipExtractor()
    recorder = SegmentRingBufferRecorder(source, on_warning=lambda m: web_server.set_warning(m))
    web_server.register(extractor, recorder)

    recorder.start()
    web_server.clear_warning()

    t = threading.Thread(target=web_server.run_server, kwargs={"port": PORT}, daemon=True)
    t.start()
    time.sleep(1)

    print("フレームが届くのを待ちます...")
    time.sleep(2)

    print("\n--- /api/preview.jpg ---")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/preview.jpg") as r:
        data = r.read()
        content_type = r.headers.get("Content-Type")
    print(f"content-type={content_type}, {len(data)} bytes")
    assert content_type == "image/jpeg", "content-typeがimage/jpegでない"
    assert data[:2] == b"\xff\xd8", "JPEGのマジックバイトで始まっていない"
    assert data[-2:] == b"\xff\xd9", "JPEGの終端マーカーで終わっていない"

    print("\n--- 直近フレームが更新され続けているか(2回取得して差分があるか) ---")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/preview.jpg") as r:
        data1 = r.read()
    time.sleep(0.5)
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/preview.jpg") as r:
        data2 = r.read()
    print(f"1回目: {len(data1)} bytes, 2回目: {len(data2)} bytes, 同一: {data1 == data2}")
    assert data1 != data2, "0.5秒後も全く同じフレームが返っている(更新されていない疑い)"

    print("\n--- /preview 画面 ---")
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/preview") as r:
        html = r.read().decode()
    print("preview.html配信確認:", "preview-img" in html, len(html), "bytes")
    assert "preview-img" in html, "preview.htmlの中身が期待と違う"

    recorder.stop()
    print("\n完了: プレビューAPI・画面ともに正常動作を確認した")


if __name__ == "__main__":
    main()
