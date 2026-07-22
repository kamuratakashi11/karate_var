"""
起動エントリーポイント(PC側で実行)。

- 録画エンジン(リングバッファ)を起動
- Webサーバーを起動(iPad/PCブラウザから監査画面にアクセス可能にする)
- 記録員の「やめ」操作を受け付ける
  --input-mode enter  : キーボードのEnterキーで代用(動作確認・単体テスト用)
  --input-mode button : 業者のUSB早押しボタン(キーボード入力として認識)を監視
- 「次の試合へ」操作(キーボードのNキー)を、入力モードに関係なく常時受け付ける

使い方:
  カメラ未着手時: python3 main.py --mock
  カメラ到着後  : python3 main.py --camera 0
  ボタン連動時  : python3 main.py --camera 0 --input-mode button
                 (事前に tools/detect_keyboard_key.py で認識確認・config.py設定が必要)
"""

import argparse
import threading
import time
import urllib.request

from camera_source import MockCameraSource, RealCameraSource
from recorder import SegmentRingBufferRecorder
from clip_extractor import ClipExtractor
import web_server
import audit_log


def _wait_for_web_server(port, timeout_sec=15):
    """監査Webサーバーが実際にHTTP応答を返すようになるまで待つ(起動失敗の検知用)"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="カメラなしでダミー映像を使う")
    parser.add_argument("--camera", type=int, default=0, help="実カメラのデバイス番号")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--input-mode", choices=["enter", "button"], default="enter",
                         help="『やめ』操作の受付方法。enter=Enterキー(動作確認用)、"
                              "button=業者のUSBボタン連動")
    args = parser.parse_args()

    audit_log.log_event("system_start", input_mode=args.input_mode,
                         mock=args.mock, camera=args.camera)

    if args.mock:
        print("[main] ダミー映像モードで起動します")
        source = MockCameraSource()
    else:
        print(f"[main] 実カメラ(device={args.camera})で起動します")
        source = RealCameraSource(args.camera)

    extractor = ClipExtractor()

    def on_warning(msg):
        print(f"[警告] {msg}")
        web_server.set_warning(msg)
        audit_log.log_event("recording_warning", message=msg)

    recorder = SegmentRingBufferRecorder(source, on_warning=on_warning)
    web_server.register(extractor, recorder)

    recorder.start()
    web_server.clear_warning()

    # Webサーバーは別スレッドで起動(iPad/PCブラウザからアクセス可能にする)
    server_thread = threading.Thread(
        target=web_server.run_server, kwargs={"port": args.port}, daemon=True
    )
    server_thread.start()

    # daemon threadの中でapp.run()がポート競合・権限エラー等で起動失敗しても、
    # 例外は当該スレッド内で握りつぶされ、メインスレッド(録画処理)はそれと
    # 気づかず動き続けてしまう(実機検証で実際にこの現象を確認した:
    # WinError 10013でbindに失敗したが、録画自体は正常に継続していたため、
    # 誰も気づけないまま監査画面にアクセスできない状態が続いた)。
    # そのため実際にHTTP応答があるかを起動直後に確認し、確認できなければ
    # 大きな警告として出す。
    if _wait_for_web_server(args.port):
        print(f"[main] 監査画面: http://<このPCのIPアドレス>:{args.port}/ にiPadからアクセスしてください")
        print(f"[main] カメラプレビュー(セッティング確認用): "
              f"http://<このPCのIPアドレス>:{args.port}/preview")
    else:
        message = (f"監査Webサーバー(ポート{args.port})の起動を確認できませんでした。"
                    "ポートの競合・ファイアウォール等が原因の可能性があります。"
                    "iPadから監査画面にアクセスできません。")
        print(f"[main] ★★★ 警告 ★★★ {message}")
        web_server.set_warning(message)
        audit_log.log_event("web_server_start_failed", port=args.port)

    def trigger_yame():
        try:
            clip_path = extractor.extract_on_yame()
            print(f"[やめ] クリップ確定: {clip_path}")
            audit_log.log_event("clip_created", clip=clip_path)
        except Exception as e:
            print(f"[エラー] クリップ抽出に失敗しました: {e}")
            audit_log.log_event("clip_error", error=str(e))

    def on_key_event(event_type, state):
        audit_log.log_event(event_type, timer_state=state)

    def trigger_next_match():
        new_match = extractor.next_match()
        print(f"[試合] 次の試合へ: 試合{new_match}")
        audit_log.log_event("match_advanced", match=new_match)

    from key_listener import NextMatchKeyListener
    next_match_listener = NextMatchKeyListener(on_next_match=trigger_next_match)
    try:
        next_match_listener.start()
        print("[main] 「次の試合へ」キー: N (監査画面のボタンと同じ動作)")
    except RuntimeError as e:
        print(f"[エラー] 「次の試合へ」キーの監視を開始できませんでした: {e}")
        next_match_listener = None

    button_listener = None
    if args.input_mode == "button":
        from key_listener import TimerSyncedKeyListener
        button_listener = TimerSyncedKeyListener(on_yame=trigger_yame, on_event=on_key_event)
        web_server.register_timer_state_source(button_listener.get_state)
        # クリップ保護判定にタイマーの累積動作時間を使えるようにする
        extractor.running_counter_fn = button_listener.get_running_accumulator
        try:
            button_listener.start()
            print("[main] 物理ボタン(F2トグル+緊急ボタン)連動モードで待機中です")
            print(f"[main] 起動時のタイマー状態: {button_listener.get_state()} "
                  "(実際のタイマー表示と一致しているか必ず確認してください)")
        except RuntimeError as e:
            print(f"[エラー] {e}")
            print("[main] Enterキーモードにフォールバックします")
            args.input_mode = "enter"

    if args.input_mode == "enter":
        print("[main] 記録員操作: Enterキーで『やめ』(直近6秒を確定クリップとして保存)")

    print("[main] 終了: Ctrl+C")

    try:
        if args.input_mode == "enter":
            while True:
                line = input()
                # 何も入力せずEnterのみ = 「やめ」。Nキーを押すと、その文字が
                # このinput()のバッファに残ったままEnter待ちになることがあるが、
                # Nキー自体はNextMatchKeyListenerが別途(押した瞬間に)処理済みなので、
                # ここで空でない入力まで「やめ」として扱うと二重発火してしまう。
                # そのため、本当に何も入力しなかった場合だけ「やめ」を発火する。
                if line.strip() == "":
                    trigger_yame()
        else:
            # ボタン監視は別スレッドで動いているので、メインスレッドは待機するだけ
            while True:
                threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[main] 終了処理中...")
        if button_listener:
            button_listener.stop()
        if next_match_listener:
            next_match_listener.stop()
        recorder.stop()
        audit_log.log_event("system_stop")
        print("[main] 終了しました")


if __name__ == "__main__":
    main()

