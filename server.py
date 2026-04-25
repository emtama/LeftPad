import webview
import threading
import asyncio
import json
import secrets
import os
import queue
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler
import pyautogui
import pyperclip
import websockets
import socket
import qrcode
import io
import base64

# --- 設定 (元コードを継承) ---
HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GESTURE_LABELS_FILE = os.path.join(BASE_DIR, "gesture_labels.json")
GESTURES_FILE = os.path.join(BASE_DIR, "gesture_shortcuts.json")
ACCESS_TOKEN = secrets.token_urlsafe(16)
WS_BROADCAST_QUEUE = queue.Queue()  # WebSocketブロードキャスト用のスレッドセーフなキュー

# 色の設定ファイル（UIのテーマカラーなどを定義）
def load_colors():
    if os.path.exists(COLORS_FILE):
        with open(COLORS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    # ファイルがない場合のデフォルト値
    return {
        "bg": "#0d0e11", "surface": "#16181e", "accent": "#e8ff47",
        "accent2": "#47c4ff", "text": "#e4e6ee", "border": "#2a2d36", "danger": "#ff5c5c"
    }

COLORS_FILE = os.path.join(BASE_DIR, "colors.json")
COLORS = load_colors()

# ══════════════════════════════════════════════
#  ログ設定（ここを追加）
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("leftpad")
# WebSocketサーバーのログをUIに表示するためのロガーハンドラー
class WebviewLogHandler(logging.Handler):
    def __init__(self, window):
        super().__init__()
        self.window = window

    def emit(self, record):
        log_entry = self.format(record)
        # JavaScriptの addLog 関数を呼び出す（記号などはエスケープ）
        safe_log = log_entry.replace("'", "\\'").replace("\n", " ")
        try:
            # ウィンドウが生成された後に実行
            self.window.evaluate_js(f"addLog('{safe_log}')")
        except:
            pass


# ═════════════════════════════════════════════
# ジェスチャーの日本語ラベル（UI表示用）
# ═════════════════════════════════════════════
with open(GESTURE_LABELS_FILE, "r", encoding="utf-8") as f:
    GESTURE_LABELS_JP = json.load(f)
GESTURE_KEYS = list(GESTURE_LABELS_JP.keys())

# デフォルトのジェスチャー・ショートカット対応（全てからっぽ）
DEFAULT_GESTURES = {
    key: "" for key in GESTURE_KEYS
}

# --------------------------------------------------
# キーの実行
# --------------------------------------------------
def execute_keys(keys: list[str]) -> None:
    pyautogui.PAUSE = 0.02  # 20ミリ秒待つ
    
    if not keys:
        return

    try:
        if len(keys) == 1:
            # 単一キー (例: "a", "enter")
            pyautogui.press(keys[0])
        else:
            # 複数キーの同時押し (例: ["ctrl", "c"])
            pyautogui.hotkey(*keys)
    except Exception as e:
        LOGGER.error(f"キー入力実行エラー: {e}")

# ══════════════════════════════════════════════
#  接続クライアント
# ══════════════════════════════════════════════
connected_clients: set = set()
connected_client_infos: dict = {}
APP_SETTINGS = {
    "vibration_enabled": True,
}

# --- ロジック関数 ---
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except: return "127.0.0.1"

def load_json(path):
    if not os.path.exists(path): return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

# ===========================================
# PC側UIを作るpywebviewクラス-
# ===========================================
# server.html の JavaScript からは window.pywebview.api.関数名() で呼び出せます
class JSApi:
    def __init__(self):
        self.ip = None  # IPアドレスは起動時に取得してURLを生成
        self.http_url = f"http://{self.ip}:{HTTP_PORT}/smartphone.html?token={ACCESS_TOKEN}"
        self.ws_url = f"ws://{self.ip}:{WS_PORT}"

    # 起動時にUIへ必要な情報を渡す関数
    def get_init_data(self):
        if self.ip is None:
            self.ip = get_local_ip()
            self.http_url = f"http://{self.ip}:{HTTP_PORT}/smartphone.html?token={ACCESS_TOKEN}"
            self.ws_url = f"ws://{self.ip}:{WS_PORT}"
        return {
            "http_url": self.http_url,
            "ws_url": self.ws_url,
            "qr_image": self._generate_qr_base64(self.http_url),
            "gestures": load_json(GESTURES_FILE),
            "labels": load_json(GESTURE_LABELS_FILE),
            "vibration": APP_SETTINGS["vibration_enabled"],
            "colors": COLORS
        }
    
    # QRコードを生成してBase64エンコードする関数
    def _generate_qr_base64(self, url):
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        img = qr.make_image(fill_color="#0d0e11", back_color="#e8ff47")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    # server.html から呼び出される。ジェスチャーとキーの対応を保存する。
    def save_shortcut(self, gesture_key, keys_list):
        """
        JavaScript側から送られてきたキーの配列を保存する。
        空配列 [] が送られてきた場合は削除として扱う。
        """
        try:
            data = load_json(GESTURES_FILE)
            
            # 1. データのバリデーションと正規化
            if not isinstance(keys_list, list):
                keys_list = []
            
            # PyAutoGUIで使いやすいよう、すべて小文字に変換して保存
            # (server_old.py の execute_keys ロジックに合わせる)
            normalized_keys = [str(k).lower() for k in keys_list]
            
            data[gesture_key] = normalized_keys
            
            # 2. ファイル保存
            with open(GESTURES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # 3. WebSocket経由でスマホにも即座に通知
            msg = json.dumps({"type": "gestures", "data": data})
            WS_BROADCAST_QUEUE.put(msg)
            
            LOGGER.info(f"更新: {gesture_key} -> {normalized_keys}")
            return True
        except Exception as e:
            LOGGER.error(f"保存エラー: {e}")
            return False

    # 振動設定をserver.htmlから受信して、スマホ側に送信する関数
    def toggle_vibration(self, enabled):
        APP_SETTINGS["vibration_enabled"] = enabled
        msg = json.dumps({"type": "vibration_setting_updated", "data": enabled})
        WS_BROADCAST_QUEUE.put(msg)

    # 指定されたテキストをクリップボードにコピーする関数
    def copy_to_clipboard(self, text):
        # pyperclipを使うのが確実ですが、pyautoguiでも代用可能です
        # ここではより一般的なpyperclipを想定（または標準のtkinter経由）
        pyperclip.copy(text)
        LOGGER.info(f"URLをコピーしました: {text}")
        return True

# =============================================
# スマホ側とのWebSocket/http通信を行う
# ================================================
# Websocketハンドラ（スマホ側との通信）
async def ws_handler(websocket):

    # クライアント情報（IPアドレスとポート番号）を取得
    client = websocket.remote_address

    # ── 認証メッセージ検証（接続直後） ─────────────
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=8)
        data = json.loads(raw)
    except asyncio.TimeoutError:
        LOGGER.warning(f"認証タイムアウト: {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    except json.JSONDecodeError:
        LOGGER.warning(f"認証失敗(JSON不正): {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    except websockets.exceptions.ConnectionClosed:
        return
    # -- 認証メッセージの形式を検査 --
    if data.get("type") != "auth":
        LOGGER.warning(f"認証失敗(type不正): {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    # -- トークンを検査 --
    token = data.get("token", "")
    if token != ACCESS_TOKEN:
        LOGGER.warning(f"不正アクセス拒否: {client[0]} (token不一致)")
        await websocket.send(json.dumps({"type": "auth", "ok": False}))
        await websocket.close(4001, "Unauthorized")
        return

    await websocket.send(json.dumps({"type": "auth", "ok": True}))
    
    # ── 認証成功 ─────────────
    LOGGER.info(f"WS 接続: {client[0]}:{client[1]}")
    connected_clients.add(websocket)
    connected_client_infos[websocket] = f"{client[0]}:{client[1]}"

    # （随時）クライアントからのメッセージを待機して処理するループ。接続が切れるまで続く。
    try:
        # クライアントからのメッセージを待機。メッセージの形式は全てJSONで、"type"フィールドで種類を判別する。
        async for message in websocket:# データが来るたびに一回実行するということ。
            # 受信メッセージは全てJSON形式を想定。コマンドの種類は "type" フィールドで判別する。
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "ok": False, "error": "invalid json"}))
                continue
            msg_type = data.get("type", "")   # "get_shortcuts" / "update_shortcut" / ""
            # スマホ側のバイブレーション対応状況を受け取る
            if msg_type == "vibration_status":
                supported = bool(data.get("supported", False))
                allowed = bool(data.get("allowed", False))
                if not supported or not allowed:
                    LOGGER.warning(f"端末バイブレーション警告: {client[0]} (supported={supported}, allowed={allowed})")
                else:
                    LOGGER.info(f"端末バイブレーション状態: {client[0]} 利用可能")
                continue          
            # "update_setting" → アプリの設定値を更新する（例: vibration_enabled）
            elif msg_type == "update_setting":
                key = data.get("key")
                value = data.get("value")
                if key in APP_SETTINGS and isinstance(value, bool):
                    APP_SETTINGS[key] = value
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": True, "key": key, "value": value}))
                else:
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": False}))
                continue

            # ── ジェスチャーコマンド実行 ────────────────
            gesture_name = data.get("gesture", "").strip()
            gesture_label = GESTURE_LABELS_JP.get(gesture_name, gesture_name)
            # ジェスチャー名が不正 
            if not gesture_name or gesture_name not in gestures:
                await websocket.send(json.dumps({"type": "error", "ok": False, "error": "no gesture"}))
                continue
            # ジェスチャーに対応するキー配列を取得。形式が不正ならエラーを返す
            keys = gestures[gesture_name]
            if not isinstance(keys, list) or not keys:
                await websocket.send(json.dumps({"type": "error", "ok": False, "error": "invalid gesture"}))
                continue
            # キー実行。エラーが出たらクライアントに返す（例: 無効なキー指定）
            try:
                execute_keys(keys)
                LOGGER.info(f"[ジェスチャー:{gesture_label}] {gesture_name:30s} → {'+'.join(keys)}")
                await websocket.send(json.dumps({"type": "gesture_result", "ok": True, "gesture": gesture_name, "keys": keys}))
            except Exception as e:
                LOGGER.error(f"キー実行エラー: {e}")
                await websocket.send(json.dumps({"type": "gesture_result", "ok": False, "error": str(e)}))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        LOGGER.warning(f"WS 異常切断: {e}")
    finally:
        connected_clients.discard(websocket)
        connected_client_infos.pop(websocket, None)
        LOGGER.info(f"WS 切断: {client[0]}")

# websocketサーバーはasyncioで動かす必要があるため、専用のイベントループを作成して実行します
def run_ws():
    # 1. 新しいイベントループを作成
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. サーバーを非同期でセットアップする内部関数
    async def main():
        # websockets.serve は async with で使うか、await する必要があります
        async with websockets.serve(ws_handler, HOST, WS_PORT):
            LOGGER.info(f"WebSocket サーバー起動: ポート {WS_PORT}")
            await asyncio.Future()  # 永久に待機

    # 3. ループを実行
    try:
        loop.run_until_complete(main())
    except Exception as e:
        LOGGER.error(f"WebSocketサーバーエラー: {e}")
    finally:
        loop.close()

# HTTP
def run_http():
    server = HTTPServer((HOST, HTTP_PORT), SimpleHTTPRequestHandler)
    server.serve_forever()

# ウィンドウが表示された後に実行される関数
# ここでサーバーを起動することで、UIが先に表示されてログも見えるようになる
def initialize(window):
    threading.Thread(target=run_ws, daemon=True).start()
    threading.Thread(target=run_http, daemon=True).start()
    LOGGER.info("各サーバーを開始しました")

# --- メイン ---
if __name__ == '__main__':
    api = JSApi()
    
    window = webview.create_window(
       'LeftPad Server', 
        url='server.html', 
        js_api=api,
        width=1100, height=750,
        background_color=COLORS['bg'],
        maximized=True,
        fullscreen=False
    )

    # ログの設定（ここを追加）
    handler = WebviewLogHandler(window)
    # %(asctime)s を使い、時刻の形式は datefmt で指定するのが正解です
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
    LOGGER.addHandler(handler)

    webview.start(initialize, window, debug=True)
