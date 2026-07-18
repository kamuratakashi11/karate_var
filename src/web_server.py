"""
監査用Webサーバー(PC側で常駐)。

iPad(またはPC自身のブラウザ)からLAN経由で以下にアクセスできるようにする:
  GET /                  監査用画面(index.html)
  GET /api/clips         現在保持中のクリップ一覧(JSON)
  GET /clips/<filename>  クリップ動画本体(mp4)
  GET /api/status        カメラ警告状態(録画が止まっていないか)
  POST /api/clips/<filename>/save  指定クリップを永久保存(FIFO対象外)にする
  GET /api/saved         永久保存済みクリップ一覧(JSON)
  GET /saved/<filename>  永久保存済みクリップ動画本体(mp4)

「やめ」操作自体はPC側の記録員が行うため、iPad側には
クリップを追加・削除するAPIは設けない(閲覧専用)。ただし
「このクリップを永久保存する」操作だけは、良いプレイを消さずに
残したいという運用上の要望のため例外的に認めている
(FIFOで削除される data/clips/ から data/saved/ へのコピーのみで、
既存クリップの削除・上書きは一切行わない)。
"""

import logging
import os
from flask import Flask, jsonify, send_from_directory, render_template

from config import CLIPS_DIR, SAVED_DIR, COURT_NAME
import saved_clips
import audit_log

app = Flask(__name__, static_folder="../static", template_folder="../static")

# main.py 側からセットされる想定のグローバル参照
# (ClipExtractor/Recorderインスタンスと、録画警告の状態を共有するため)
_clip_extractor = None
_recorder = None
_status = {"recording_ok": True, "message": ""}
_timer_state_source = None  # main.py側からTimerSyncedKeyListener.get_stateを登録する


def register(clip_extractor, recorder=None):
    global _clip_extractor, _recorder
    _clip_extractor = clip_extractor
    _recorder = recorder


def register_timer_state_source(get_state_fn):
    """
    main.pyから TimerSyncedKeyListener.get_state を渡してもらい、
    監査画面がいつでも現在の追跡状態(running/stopped)を確認できるようにする。
    (F2ボタン方式を使わない --input-mode enter の場合は呼ばれない)
    """
    global _timer_state_source
    _timer_state_source = get_state_fn


def set_warning(message):
    _status["recording_ok"] = False
    _status["message"] = message


def clear_warning():
    _status["recording_ok"] = True
    _status["message"] = ""


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/clips")
def api_clips():
    if _clip_extractor is None:
        return jsonify([])
    clips = _clip_extractor.list_current_clips()
    # 新しい順に並べて返す(監査画面では最新を上に出す)
    result = [
        {
            "filename": os.path.basename(p),
            "label": os.path.basename(p).replace("bar_clip_", "").replace(".mp4", ""),
        }
        for p in reversed(clips)
    ]
    return jsonify(result)


@app.route("/clips/<path:filename>")
def clip_file(filename):
    return send_from_directory(CLIPS_DIR, filename)


@app.route("/api/clips/<path:filename>/save", methods=["POST"])
def api_save_clip(filename):
    try:
        entry = saved_clips.save_clip(filename)
    except FileNotFoundError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    audit_log.log_event("clip_saved", original_clip=entry["original_clip"],
                         saved_filename=entry["filename"])
    return jsonify({"ok": True, **entry})


@app.route("/api/saved")
def api_saved():
    return jsonify(saved_clips.list_saved_clips())


@app.route("/saved/<path:filename>")
def saved_file(filename):
    return send_from_directory(SAVED_DIR, filename)


@app.route("/api/status")
def api_status():
    result = dict(_status)
    if _timer_state_source is not None:
        result["timer_sync_state"] = _timer_state_source()
    else:
        result["timer_sync_state"] = None
    return jsonify(result)


@app.route("/api/health")
def api_health():
    """
    中央監視ダッシュボード専用。映像データは一切含まず、
    「録画が正常に動いているか」の軽量な死活情報のみを返す。
    """
    base = {
        "court": COURT_NAME,
        "recording_ok": False,
        "last_frame_age_sec": None,
        "uptime_sec": 0,
        "frame_count": 0,
        "message": _status.get("message", ""),
    }
    if _recorder is not None:
        base.update(_recorder.get_health())
    return jsonify(base)


def run_server(host="0.0.0.0", port=5000):
    # Werkzeugは既定で1リクエストごとにINFOログ(アクセスログ)を標準出力に書く。
    # 実機の長時間安定性テストで、この大量のログ出力がWindows上のコンソール
    # 出力(colorama)の内部ロックを介して詰まり、監査Webサーバー全体が
    # ハングする現象を確認した(録画自体は継続するため誰も気づけない)。
    # 監査画面のアクセス頻度・8コート死活監視のポーリング頻度を考えると
    # 大会当日にも起こり得るため、通常のアクセスログは出力しない。
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host=host, port=port, threaded=True)
