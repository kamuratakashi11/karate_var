"""
ラウンド4で追加した「試合」タグ・一括削除機能のヘッドレステスト。

  1. クリップに現在の試合番号が正しく付与されるか
  2. 「次の試合へ」(next_match)しても、既存クリップはCLIP_SLOTSの
     デフォルト(20)の範囲内では自動的に消えないか(FIFO件数の安全弁
     としては働くが、試合が変わっただけでは削除されないことの確認)
  3. /api/match/current, /api/match/next, /api/clips/clear のAPIが
     期待通りに動くか
  4. 一括削除(clear_all)が、ライブクリップ(data/clips/)だけを消し、
     保存済み(data/saved/)には一切影響しないか
"""

import json
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

PORT = 5062


def main():
    source = MockCameraSource(event_interval_sec=4.0)
    extractor = ClipExtractor()
    web_server.register(extractor)

    recorder = SegmentRingBufferRecorder(source, on_warning=lambda m: web_server.set_warning(m))
    recorder.start()
    web_server.clear_warning()

    t = threading.Thread(target=web_server.run_server, kwargs={"port": PORT}, daemon=True)
    t.start()
    time.sleep(1)

    print("録画開始... 15秒間バッファを溜めます")
    time.sleep(15)

    print("\n--- 試合1で『やめ』を2回 ---")
    clip1 = extractor.extract_on_yame()
    time.sleep(3)
    clip2 = extractor.extract_on_yame()

    current = extractor.list_current_clips()
    print("現在のクリップ:", current)
    assert all(c["match"] == 1 for c in current), "試合1のクリップの試合番号が1になっていない"

    print("\n--- 『次の試合へ』API呼び出し ---")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/match/next", method="POST")
    with urllib.request.urlopen(req) as r:
        result = json.load(r)
    print("next結果:", result)
    assert result["ok"] and result["match"] == 2, "次の試合への遷移が期待通りでない"

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/match/current") as r:
        cur = json.load(r)
    assert cur["match"] == 2, "現在の試合番号取得が2になっていない"

    time.sleep(3)
    print("\n--- 試合2で『やめ』を1回 ---")
    clip3 = extractor.extract_on_yame()

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/clips") as r:
        clips = json.load(r)
    print("/api/clips:", clips)
    matches_seen = {c["match"] for c in clips}
    assert matches_seen == {1, 2}, f"試合1・2のクリップが両方見えているべき(実際: {matches_seen})"
    assert os.path.exists(clip1), "試合が変わっただけでclip1が消えてしまっている(CLIP_SLOTSの安全弁が働きすぎ)"

    print("\n--- clip3を保存し、試合番号が引き継がれるか確認 ---")
    clip3_name = os.path.basename(clip3)
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/clips/{clip3_name}/save", method="POST")
    with urllib.request.urlopen(req) as r:
        save_result = json.load(r)
    print("保存結果:", save_result)
    assert save_result["match"] == 2, "保存されたクリップの試合番号が引き継がれていない"

    print("\n--- ライブクリップの一括削除 ---")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/clips/clear", method="POST")
    with urllib.request.urlopen(req) as r:
        clear_result = json.load(r)
    print("削除結果:", clear_result)
    assert clear_result["ok"] and clear_result["cleared"] == 3, "一括削除の件数が期待と違う"

    assert not os.path.exists(clip1), "一括削除後もclip1が残っている"
    assert not os.path.exists(clip2), "一括削除後もclip2が残っている"
    assert not os.path.exists(clip3), "一括削除後もclip3が残っている"

    saved_path = os.path.join(os.path.dirname(clip3), "..", "saved", save_result["filename"])
    saved_path = os.path.normpath(saved_path)
    assert os.path.exists(saved_path), "一括削除で保存済みコピーまで消えてしまっている(重大なバグ)"

    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/clips") as r:
        clips_after = json.load(r)
    assert clips_after == [], "一括削除後もAPIがクリップを返している"

    recorder.stop()
    print("\n完了: 試合タグ付け・引き継ぎ・一括削除(保存済みは無事)を確認した")


if __name__ == "__main__":
    main()
