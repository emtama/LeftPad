import webview  # python 3.14だと使えない。3.13で使うこと。
import threading
import asyncio
import json
import secrets
import os
import queue
import logging
import ssl
from http.server import HTTPServer, SimpleHTTPRequestHandler
import pyautogui
import pyperclip
import websockets
import socket
import qrcode
import io
import base64

# ══════════════════════════════════════════════
#  グローバル変数の初期化
# ══════════════════════════════════════════════
HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8080
ACCESS_TOKEN = secrets.token_urlsafe(16)

WS_BROADCAST_QUEUE = queue.Queue()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 証明書ファイルのパス
SSL_CERT_FILE_PATH = os.path.join(BASE_DIR, "cert.pem")
SSL_KEY_FILE_PATH = os.path.join(BASE_DIR, "key.pem")

GESTURE_LABELS_JP_PATH = os.path.join(BASE_DIR, "../share/gesture_labels.json")
GESTURE_SHORTCUTS_PATH = os.path.join(BASE_DIR, "../share/gesture_shortcuts.json")

LOGGER = None
GESTURE_LABELS_JP = {}
GESTURE_KEYS = []
GESTURE_SHORTCUTS = {}
CONNECTED_CLIENTS: set = set()
CONNECTED_CLIENTS_INFOS: dict = {}
APP_SETTINGS = {
    "vibration_enabled": True,
}

# ══════════════════════════════════════════════
#  SSL/HTTPS 判定ユーティリティ
# ══════════════════════════════════════════════

def is_ssl_available():
    """証明書ファイルが存在するかチェック"""
    available = os.path.exists(SSL_CERT_FILE_PATH) and os.path.exists(SSL_KEY_FILE_PATH)
    if not available and LOGGER:
        LOGGER.warning("SSL証明書(cert.pem/key.pem)が見つかりません。HTTPモードで動作します。")
    return available

def get_ssl_context():
    """HTTPS/WSS用のSSLコンテキストを作成。ファイルがない場合はNoneを返す"""
    if not is_ssl_available():
        return None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=SSL_CERT_FILE_PATH, keyfile=SSL_KEY_FILE_PATH)
        return context
    except Exception as e:
        if LOGGER: LOGGER.error(f"SSLコンテキスト作成エラー: {e}")
        return None

# ══════════════════════════════════════════════
#  ローカル：ユーティリティ
# ══════════════════════════════════════════════
def load_json(path, encoding="utf-8") -> dict:
    try:
        if not os.path.exists(path): return {}
        with open(path, "r", encoding=encoding) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        if LOGGER: LOGGER.error(f"{path} の読み込みに失敗: {e}")
        return {}

# ══════════════════════════════════════════════
#  ローカル：ログ設定
# ══════════════════════════════════════════════
LOGGER = logging.getLogger("leftpad")

class WebviewLogHandler(logging.Handler):
    def __init__(self, window):
        super().__init__()
        self.window = window
    def emit(self, record):
        log_entry = self.format(record)
        safe_log = log_entry.replace("'", "\\'").replace("\n", " ")
        try: self.window.evaluate_js(f"addLog('{safe_log}')")
        except: pass

# ══════════════════════════════════════════════
# ローカル：キーの実行
# ══════════════════════════════════════════════
def execute_keys(keys: list[str]) -> None:
    pyautogui.PAUSE = 0.02
    if not keys: return
    try:
        if len(keys) == 1: pyautogui.press(keys[0])
        else: pyautogui.hotkey(*keys)
    except Exception as e:
        LOGGER.error(f"キー入力実行エラー: {e}")

# ══════════════════════════════════════════════
#  ネットワーク：ローカルIP
# ══════════════════════════════════════════════
def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except: return "127.0.0.1"

# データの読み込み
GESTURE_LABELS_JP = load_json(GESTURE_LABELS_JP_PATH)
GESTURE_KEYS = list(GESTURE_LABELS_JP.keys())

def load_gesture_shortcuts():
    data = load_json(GESTURE_SHORTCUTS_PATH)
    for key in list(data.keys()):
        if key not in GESTURE_LABELS_JP: del data[key]
    return data

GESTURE_SHORTCUTS = load_gesture_shortcuts()

# ══════════════════════════════════════════════
# PC側GUI API
# ══════════════════════════════════════════════
class JSApi:
    PWA_START_HTML_PATH = r"../docs/index.html"
    def __init__(self):
        self.ip = None
        self.http_url = None
        self.ws_url = None

    def get_init_data(self, qr_fill_color, qr_back_color):
        if self.ip is None:
            self.ip = get_local_ip()
            use_ssl = is_ssl_available()
            
            # SSLの有無でプロトコルを切り替え
            scheme_http = "https" if use_ssl else "http"
            scheme_ws = "wss" if use_ssl else "ws"
            
            self.http_url = f"{scheme_http}://{self.ip}:{HTTP_PORT}/{JSApi.PWA_START_HTML_PATH}?ip={self.ip}&token={ACCESS_TOKEN}"
            self.ws_url = f"{scheme_ws}://{self.ip}:{WS_PORT}"
            
        return {
            "http_url": self.http_url,
            "ws_url": self.ws_url,
            "qr_image": self._generate_qr_base64(self.http_url, qr_fill_color, qr_back_color),
            "gesture_shortcuts": GESTURE_SHORTCUTS,
            "gesture_labels": GESTURE_LABELS_JP,
            "vibration": APP_SETTINGS["vibration_enabled"]
        }
    
    def _generate_qr_base64(self, url, fill_color, back_color):
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        img = qr.make_image(fill_color=fill_color, back_color=back_color)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    def save_shortcut(self, gesture_key, keys_list):
        try:            
            if not isinstance(keys_list, list): keys_list = []
            normalized_keys = [str(k).lower() for k in keys_list]            
            GESTURE_SHORTCUTS[gesture_key] = normalized_keys
            with open(GESTURE_SHORTCUTS_PATH, "w", encoding="utf-8") as f:
                json.dump(GESTURE_SHORTCUTS, f, ensure_ascii=False, indent=2)
            msg = json.dumps({"type": "gesture_shortcuts", "data": GESTURE_SHORTCUTS})
            WS_BROADCAST_QUEUE.put(msg)
            return True
        except Exception as e:
            LOGGER.error(f"保存エラー: {e}")
            return False

    def toggle_vibration(self, enabled):
        APP_SETTINGS["vibration_enabled"] = enabled
        msg = json.dumps({"type": "vibration_setting_updated", "data": enabled})
        WS_BROADCAST_QUEUE.put(msg)

    def copy_to_clipboard(self, text):
        try:
            pyperclip.copy(text)
            return True
        except: return False

# ══════════════════════════════════════════════
# スマホ側との WS/WSS 通信
# ══════════════════════════════════════════════
def inform_ui_connection_stats():
    stats = {"count": len(CONNECTED_CLIENTS), "ips": list(CONNECTED_CLIENTS_INFOS.values())}
    window.evaluate_js(f"connectionStatsUpdate({json.dumps(stats)})")

async def ws_handler(websocket):
    client_ip, client_port = websocket.remote_address
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        data = json.loads(raw)
        if data.get("type") != "auth" or data.get("token") != ACCESS_TOKEN:
            await websocket.close(4001, "Unauthorized")
            return
    except:
        await websocket.close(4001, "Unauthorized")
        return

    await websocket.send(json.dumps({"type": "auth", "ok": True}))
    CONNECTED_CLIENTS.add(websocket)
    CONNECTED_CLIENTS_INFOS[websocket] = f"{client_ip}:{client_port}"
    inform_ui_connection_stats()

    await websocket.send(json.dumps({
        "type": "initial_auth_setup", 
        "gesture_shortcuts": GESTURE_SHORTCUTS,
        "gesture_labels": GESTURE_LABELS_JP,
        "vibration_setting": APP_SETTINGS.get("vibration_enabled")
    }))
    
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type")
                if msg_type == "gesture_detected":
                    gesture_name = data.get("gesture_name")
                    keys = GESTURE_SHORTCUTS.get(gesture_name)
                    if keys:
                        execute_keys(keys)
                        LOGGER.info(f"[WS] {gesture_name} -> {'+'.join(keys)}")
            except Exception as e:
                LOGGER.error(f"WSメッセージ処理エラー: {e}")
    finally:
        CONNECTED_CLIENTS.discard(websocket)
        CONNECTED_CLIENTS_INFOS.pop(websocket, None)
        inform_ui_connection_stats()

def run_ws():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    async def main():
        ssl_context = get_ssl_context()
        # ssl_context があれば WSS、なければ WS として起動
        async with websockets.serve(ws_handler, HOST, WS_PORT, ssl=ssl_context):
            protocol = "WSS" if ssl_context else "WS"
            LOGGER.info(f"{protocol} サーバー起動: ポート {WS_PORT}")
            await asyncio.Future()
    loop.run_until_complete(main())

# ══════════════════════════════════════════════
# HTTP/HTTPS サーバー
# ══════════════════════════════════════════════
def run_http():
    server = HTTPServer((HOST, HTTP_PORT), SimpleHTTPRequestHandler)
    ssl_context = get_ssl_context()
    if ssl_context:
        server.socket = ssl_context.wrap_socket(server.socket, server_side=True)
        LOGGER.info(f"HTTPS サーバー起動: ポート {HTTP_PORT}")
    else:
        LOGGER.info(f"HTTP サーバー起動: ポート {HTTP_PORT}")
    server.serve_forever()

def initialize_servers(window):
    threading.Thread(target=run_ws, daemon=True).start()
    threading.Thread(target=run_http, daemon=True).start()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    api = JSApi()
    window = webview.create_window('LeftPad Server', url='server.html', js_api=api, width=1100, height=750)
    handler = WebviewLogHandler(window)
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
    LOGGER.addHandler(handler)
    webview.start(initialize_servers, window)