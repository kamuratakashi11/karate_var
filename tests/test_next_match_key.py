"""
「次の試合へ」ホットキー(NextMatchKeyListener)のロジック確認。
pynput・カメラ・ffmpegいずれも使わない、純粋なロジックだけの高速テスト。

  1. 設定したキー(既定"n")を押すとコールバックが1回だけ発火するか
  2. 別のキーでは発火しないか
  3. デバウンス時間内の連打では2回目以降が無視されるか
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from key_listener import NextMatchKeyListener


def main():
    calls = []
    listener = NextMatchKeyListener(on_next_match=lambda: calls.append(time.time()))

    print("--- 'n'キー(設定キーと一致) ---")
    fired = listener.handle_key_event("n")
    print(f"発火: {fired} (期待値: True), コールバック回数: {len(calls)}")
    assert fired is True
    assert len(calls) == 1

    print("\n--- 大文字'N'(大文字小文字を区別しない) ---")
    time.sleep(0.6)  # デバウンス時間(0.5秒)を超えて待つ
    fired = listener.handle_key_event("N")
    print(f"発火: {fired} (期待値: True), コールバック回数: {len(calls)}")
    assert fired is True
    assert len(calls) == 2

    print("\n--- 関係ないキー('x') ---")
    fired = listener.handle_key_event("x")
    print(f"発火: {fired} (期待値: False), コールバック回数: {len(calls)}")
    assert fired is False
    assert len(calls) == 2

    print("\n--- デバウンス確認(直後にもう一度'n') ---")
    fired = listener.handle_key_event("n")
    print(f"発火: {fired} (期待値: False=チャタリング防止), コールバック回数: {len(calls)}")
    assert fired is False
    assert len(calls) == 2

    print("\n完了: 全て期待通り")


if __name__ == "__main__":
    main()
