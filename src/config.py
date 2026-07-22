"""
空手BAR(Bunkai/Appeal Review)システム 設定ファイル
開発者がここの値を直接編集して運用パラメータを変更する(MVP段階の想定)
"""
import os

# --- コート識別 ---
# 8コート運用時、中央監視ダッシュボードが各PCを区別するための名前。
# 各コートのPCごとにここを書き換えるか、環境変数 COURT_NAME で上書きする
# (テストや複数プロセス同時起動時の利便性のため環境変数を優先)
COURT_NAME = os.environ.get("COURT_NAME", "コート1")

# --- カメラ映像設定 ---
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
FPS = 60

# --- リングバッファ設定 ---
# ffmpegの segment マルチプレクサで何秒ごとにファイルを区切るか
SEGMENT_SECONDS = 2
# 何個分のセグメントを保持し続けるか(古いものは自動削除)
# SEGMENT_SECONDS * BUFFER_SEGMENTS が実際の常時保持秒数の目安
# 例: 2秒 x 9個 = 18秒分バッファ(CLIP_DURATION_SECONDS=10秒に対して
# 余裕を持たせている。CLIP_DURATION_SECONDSを変更した場合、
# 少なくともその1.5〜2倍程度のバッファ秒数を確保すること)
BUFFER_SEGMENTS = 9

# --- クリップ抽出設定 ---
# 「やめ」操作の瞬間から遡って何秒分を確定クリップとして切り出すか。
# ルール上は6秒だが、念のため余裕を持たせて10秒にしている。
CLIP_DURATION_SECONDS = 10.0

# 「やめ」の瞬間に書き込み中だったセグメントが完全に書き終わる(次の
# セグメントに切り替わる)まで、クリップ切り出しを待つ最大時間。
# SEGMENT_SECONDS分より少しだけ長くしておく(切り替わりのタイミング次第で
# ほぼSEGMENT_SECONDS秒近くかかることがあるため)。この待ちにより、
# 書き込み中ファイルを読んでしまう競合(=映像破損)を、精度を落とさずに
# 回避できる。タイムアウトした場合はそのまま進み、デコード検証による
# フォールバックに委ねる。
SEGMENT_CLOSE_WAIT_TIMEOUT_SEC = SEGMENT_SECONDS + 1.0

# 確定クリップを何世代分保持するか(FIFO、オフィシャルミス対応で2以上を推奨)。
# 1試合の中で複数回「やめ」が発生し、試合終了後にまとめてレビューする運用
# (空手VAR以外の競技、あるいは空手でも練習試合の振り返り等)を想定し、
# 試合中に自動的に消えてしまわないよう余裕を持った値にしてある。
# クリップの掃除は基本的に監査画面の「一括削除」ボタンで手動で行う運用とし、
# この数字はディスク容量が際限なく増え続けないための安全弁という位置づけ。
# 環境変数 CLIP_SLOTS で上書き可能(コートやシステムごとに調整したい場合)。
CLIP_SLOTS = int(os.environ.get("CLIP_SLOTS", "20"))

# クリップは作成された直後、タイマーが実際に「動作中」だった累積時間が
# ここで指定した秒数に達するまでは、FIFOによる上書き削除の対象にしない。
# (タイマー表示上の時刻ではなく、こちらのシステムが把握している
# スタート/ストップの実動作時間を基準にする。タイマー側の手動巻き戻し・
# 早送りの影響を受けないようにするため)
# これにより、ストップボタンを連打してもクリップが誤って消えることがなくなる
# (--input-mode button 使用時のみ有効。enterモードではタイマー状態を
# 追跡していないため、この保護は効かず常に通常のFIFOで動作する)
CLIP_PROTECTION_RUNNING_SECONDS = 2.0

# --- ディレクトリ設定 ---

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUFFER_DIR = os.path.join(BASE_DIR, "data", "buffer")     # ローリングセグメント置き場
CLIPS_DIR = os.path.join(BASE_DIR, "data", "clips")       # 確定済み6秒クリップ置き場(FIFOで自動削除される)
SAVED_DIR = os.path.join(BASE_DIR, "data", "saved")       # 監査担当が「保存」したクリップの永久保存置き場(FIFOの対象外)
SAVED_INDEX_PATH = os.path.join(SAVED_DIR, "index.jsonl")  # 保存クリップのメタ情報(いつ・どのコートか)

os.makedirs(BUFFER_DIR, exist_ok=True)
os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(SAVED_DIR, exist_ok=True)

# --- エンコード設定 ---
# ultrafast推奨: リアルタイム性優先。画質より安定性が重要なため
FFMPEG_PRESET = "ultrafast"
FFMPEG_CRF = "23"  # 数字が小さいほど高画質・高負荷(18-28が実用域)

# --- フォーカス設定 ---
# 試合中にオートフォーカスが迷って画面がぼやける事象への対策。
# ラウンド1のfps問題と同様、CAP_MSMFバックエンド+このカメラの組み合わせで
# 実際にこの設定が効くかはドキュメントだけでは確証が持てないため、
# 必ず実機(ライブプレビュー画面 /preview を見ながら)で確認すること。
# 既定はオートフォーカスON(今までの挙動を変えない)。現地で手動フォーカス値
# が決まるまでは、いきなりOFFにしないこと(悪いピント位置で固定されるリスクがある)。
AUTOFOCUS_ENABLED = os.environ.get("AUTOFOCUS_ENABLED", "1") not in ("0", "false", "False")
# 手動フォーカス値(カメラ・ドライバ依存の数値。目安はUVC対応カメラで0〜255程度)。
# ライブプレビュー(/preview)を見ながら現地でちょうど良い値を探して設定する。
# 未設定(None)のままAUTOFOCUS_ENABLED=0にすると、オートフォーカスを切った
# 瞬間のピント位置にそのまま固定されるだけなので、狙った値を明示するのが望ましい。
MANUAL_FOCUS_VALUE = os.environ.get("MANUAL_FOCUS_VALUE")
if MANUAL_FOCUS_VALUE is not None:
    MANUAL_FOCUS_VALUE = float(MANUAL_FOCUS_VALUE)

# --- ライブプレビュー設定 ---
# カメラのセッティング確認用(/preview画面)のJPEG画質。録画のエンコードとは
# 無関係な別経路(直近1フレームをその場でJPEG化するだけ)。
PREVIEW_JPEG_QUALITY = int(os.environ.get("PREVIEW_JPEG_QUALITY", "80"))

# --- 死活監視設定(中央ダッシュボード向け) ---
# 最後にフレームを受信してからこの秒数を超えたら「録画停止」とみなす
HEALTH_STALE_THRESHOLD_SEC = 3.0

# --- 物理ボタン連携設定(業者のタイマー用早押しボタンとの連動) ---
# ボタンはキーボード入力として割り当てられている(業者確認済み)。
# tools/detect_keyboard_key.py で実際に押して確認したキー名をここに設定する。
# 例: スペースキーなら "space"、F1キーなら "f1"、通常の文字キーならその文字そのもの。
# None のままだと、どのキーを押しても反応してしまい誤爆の危険があるため、
# 必ず現地確認の上で具体的なキー名を設定すること。
BUTTON_KEY_NAME = "f2"           # 業者確認済み: タイマー停止/開始はF2キー(トグル式)に割り当て
BUTTON_DEBOUNCE_SEC = 0.5        # 誤ってチャタリングで2回反応するのを防ぐ間隔

# F2はトグル式(スタート/ストップを交互に切り替える)なので、内部で押下回数を
# 数えて偶数回目(ストップ)だけ「やめ」処理を発火する。ただしこの方式は
# 押下の取りこぼし等で一度ズレると誤動作し続けるリスクがあるため、
# 緊急時に強制的に「やめ」を発火しつつ内部状態を再同期するための
# 専用ボタン(別キー)を用意する。tools/detect_keyboard_key.py で確認して設定すること。
BUTTON_EMERGENCY_KEY_NAME = None  # 例: "f3" (緊急やめ・再同期ボタン)

# システム起動時点でのタイマーの状態(通常は試合開始前=停止中のはず)。
# ここが実際の状態とズレていると、最初のF2押下から誤判定が始まってしまうため、
# 起動時に記録員/監査が「今タイマーが本当に止まっているか」を必ず確認すること。
TIMER_INITIAL_STATE = "stopped"   # "stopped" または "running"

# --- 「次の試合へ」キー設定 ---
# 記録員がPCのキーボードでこのキーを押すと、監査画面の「次の試合へ」ボタンと
# 同じ動作(試合番号を1つ進める)をする。F2トグル判定とは無関係な、
# --input-mode enter/button どちらでも常に有効なグローバルホットキー。
NEXT_MATCH_KEY_NAME = os.environ.get("NEXT_MATCH_KEY_NAME", "n")

# --- 監査ログ設定 ---
# F2/緊急ボタンの押下、クリップ生成の成否、タイマー追跡状態の遷移などを記録する。
# 抗議の正当性を巡って後日確認が必要になった際の証跡として使う。
AUDIT_LOG_PATH = os.path.join(BASE_DIR, "data", "audit_log.jsonl")

