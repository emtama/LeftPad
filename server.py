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
WS_BROADCAST_QUEUE = queue.Queue()

# ══════════════════════════════════════════════
#  ログ設定（ここを追加）
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("leftpad")

# 接続クライアント管理
connected_clients = set()
APP_SETTINGS = {"vibration_enabled": True}

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

# --- pywebview用 APIクラス ---
# JavaScriptから呼び出せる関数を定義します
class JSApi:
    def __init__(self):
        self.ip = get_local_ip()
        self.http_url = f"http://{self.ip}:{HTTP_PORT}/smartphone.html?token={ACCESS_TOKEN}"
        self.ws_url = f"ws://{self.ip}:{WS_PORT}"

    def get_init_data(self):
        """起動時にUIへ必要な情報を渡す"""
        qr_base64 = self._generate_qr_base64(self.http_url)
        return {
            "http_url": self.http_url,
            "ws_url": self.ws_url,
            "qr_image": qr_base64,
            "gestures": load_json(GESTURES_FILE),
            "labels": load_json(GESTURE_LABELS_FILE),
            "vibration": APP_SETTINGS["vibration_enabled"]
        }

    def _generate_qr_base64(self, url):
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        img = qr.make_image(fill_color="#0d0e11", back_color="#e8ff47")
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    def save_shortcut(self, gesture_key, keys_list):
        data = load_json(GESTURES_FILE)
        data[gesture_key] = keys_list
        with open(GESTURES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # WebSocket経由でスマホにも通知
        msg = json.dumps({"type": "gestures", "data": data})
        WS_BROADCAST_QUEUE.put(msg)
        return True

    def toggle_vibration(self, enabled):
        APP_SETTINGS["vibration_enabled"] = enabled
        msg = json.dumps({"type": "vibration_setting_updated", "data": enabled})
        WS_BROADCAST_QUEUE.put(msg)

# --- サーバー類 (スレッドで実行) ---
async def ws_handler(websocket):
    # 元のws_handlerのロジックをここに移植 (認証・キー実行など)
    # 簡易化のため中身は省略しますが、元のコードをほぼそのまま使えます
    connected_clients.add(websocket)
    try:
        async for message in websocket:
            data = json.loads(message)
            if data.get("type") == "gesture":
                # キー実行ロジック
                pass
    finally:
        connected_clients.discard(websocket)

# websocketサーバーはasyncioで動かす必要があるため、専用のイベントループを作成して実行します
def run_ws():
    # 1. 新しいイベントループを作成
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # 2. サーバーを非同期でセットアップする内部関数
    async def main():
        # websockets.serve は async with で使うか、await する必要があります
        async with websockets.serve(ws_handler, HOST, WS_PORT):
            log.info(f"WebSocket サーバー起動: ポート {WS_PORT}")
            await asyncio.Future()  # 永久に待機

    # 3. ループを実行
    try:
        loop.run_until_complete(main())
    except Exception as e:
        log.error(f"WebSocketサーバーエラー: {e}")
    finally:
        loop.close()

def run_http():
    server = HTTPServer((HOST, HTTP_PORT), SimpleHTTPRequestHandler)
    server.serve_forever()

# --- メイン ---
if __name__ == '__main__':
    api = JSApi()
    
    # サーバー類をバックグラウンドで開始
    threading.Thread(target=run_ws, daemon=True).start()
    threading.Thread(target=run_http, daemon=True).start()

    # GUI作成
    window = webview.create_window(
        'LeftPad Server', 
        url='server.html', 
        js_api=api,
        width=1200, 
        height=800,
        background_color='#0d0e11',
        maximized=True,
        fullscreen=False,
    )

    webview.start(debug=False) # 開発中はdebug=Trueで右クリック検証が使える