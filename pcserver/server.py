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

# ══════════════════════════════════════════════
#  グローバル変数の初期化
# ══════════════════════════════════════════════
HOST = "0.0.0.0"
WS_PORT = 8765
HTTP_PORT = 8080
ACCESS_TOKEN = secrets.token_urlsafe(16)

WS_BROADCAST_QUEUE = queue.Queue()  # WebSocketブロードキャスト用のスレッドセーフなキュー

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
#  ローカル：ユーティリティ
# ══════════════════════════════════════════════
# jsonファイルを読んで返す関数
def load_json(path, encoding="utf-8") -> dict:
    try:
        with open(path, "r", encoding=encoding) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"{path} must be object")
        return data
    except Exception as e:
        LOGGER.error(f"{path} の読み込みに失敗: {e}")
        return {}
    
# ══════════════════════════════════════════════
#  ローカル：ログ設定
# ══════════════════════════════════════════════

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

# ══════════════════════════════════════════════
# ローカル：キーの実行
# ══════════════════════════════════════════════
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
#  ネットワーク：ローカルIP
# ══════════════════════════════════════════════
# 自身のローカルIPアドレスを取得する関数。
def get_local_ip():
    try:
        # IPv4, UDPのソケットを作成
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # GoogleのDNSサーバに雪像
        s.connect(("8.8.8.8", 80))
        ip, port = s.getsockname()
        s.close()
        return ip
    except: 
        # 失敗時はループバックアドレスを返す
        return "127.0.0.1"

# ══════════════════════════════════════════════
#  共通：UI関係I/O
# ══════════════════════════════════════════════


# ═════════════════════════════════════════════
# 共通：ジェスチャーの日本語ラベル（UI表示用）
# ═════════════════════════════════════════════
GESTURE_LABELS_JP = load_json(GESTURE_LABELS_JP_PATH)
GESTURE_KEYS = list(GESTURE_LABELS_JP.keys())

# ══════════════════════════════════════════════
#  共通：ジェスチャー/ショートカット I/O
# ══════════════════════════════════════════════
        
# ジェスチャー/ショートカットの一覧をファイル保存する。
def save_gesture_shortcuts(data: dict) -> bool:
    try:
        with open(GESTURE_SHORTCUTS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        LOGGER.info("gesture_shortcuts.json を保存した")
        return True
    except Exception as e:
        LOGGER.error(f"gesture_shortcuts.json の保存に失敗: {e}")
        return False
    
# ジェスチャーの日本語ラベルの読み込み
def load_gesture_labels_jp(encoding="utf-8"):
    data = load_json(GESTURE_LABELS_JP_PATH, encoding)
    return data

# ジェスチャー/ショートカットファイルの読み込み
def load_gesture_shortcuts(encoding="utf-8"):
    data = load_json(GESTURE_SHORTCUTS_PATH, encoding)
    for key in list(data.keys()):
        if key not in GESTURE_LABELS_JP:
            LOGGER.warning(f"不明なジェスチャーがショートカットに存在したので削除しました: {key: data[key]}")
            del data[key]
    return data

# GESTURE_SHORTCUTSを初期化
GESTURE_SHORTCUTS = load_gesture_shortcuts()



# PC側GUIを作るpywebviewクラス-
# ══════════════════════════════════════════════
# server.html の JavaScript からは window.pywebview.api.関数名() で呼び出せます
# 送信・受信メッセージはjson形式で必ずtype属性を含む必要があります。
class JSApi:
    PWA_START_HTML_PATH = r"../docs/index.html"
    def __init__(self):
        self.ip = None  # IPアドレスは起動時に取得してURLを生成
        self.http_url = None
        self.ws_url = f"ws://{self.ip}:{WS_PORT}"

    # 起動時にUIへ必要な情報を渡す関数
    def get_init_data(self, qr_fill_color, qr_back_color):
        if self.ip is None:
            self.ip = get_local_ip()
            self.http_url = f"http://{self.ip}:{HTTP_PORT}/{JSApi.PWA_START_HTML_PATH}?ip={self.ip}&token={ACCESS_TOKEN}"
            self.ws_url = f"ws://{self.ip}:{WS_PORT}"
        return {
            "http_url": self.http_url,
            "ws_url": self.ws_url,
            "qr_image": self._generate_qr_base64(self.http_url, qr_fill_color, qr_back_color),
            "gesture_shortcuts": GESTURE_SHORTCUTS,
            "gesture_labels": GESTURE_LABELS_JP,
            "vibration": APP_SETTINGS["vibration_enabled"]
        }
    
    def _generate_qr_base64(self, url, fill_color, back_color):
        """
        QRコードを生成してBase64エンコードする関数
        """
        qr = qrcode.QRCode(box_size=10, border=2)
        qr.add_data(url)
        img = qr.make_image(fill_color=fill_color, back_color=back_color)
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode()

    def save_shortcut(self, gesture_key, keys_list):
        """
        PC側UIから送られてきたジェスチャー・ショートカットキーの配列を保存する。
        空配列 [] が送られてきた場合は削除として扱う。
        """
        try:            
            # keys_listがlistでない場合も削除として扱う。
            if not isinstance(keys_list, list):
                keys_list = []
            
            # PyAutoGUIで使いやすいよう、すべて小文字に変換して保存
            # (server_old.py の execute_keys ロジックに合わせる)
            normalized_keys = [str(k).lower() for k in keys_list]            
            
            # 2. ファイル保存
            GESTURE_SHORTCUTS[gesture_key] = normalized_keys
            with open(GESTURE_SHORTCUTS_PATH, "w", encoding="utf-8") as f:
                json.dump(GESTURE_SHORTCUTS, f, ensure_ascii=False, indent=2)
            
            # 3. WebSocket経由でスマホにも即座に通知
            msg = json.dumps({"type": "gesture_shortcuts", "data": GESTURE_SHORTCUTS})
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
        try:
            pyperclip.copy(text)
            LOGGER.info(f"URLをコピーしました: {text}")
            return True
        except Exception as e:
            LOGGER.warning(f'URLのコピーに失敗しました: {text}, {e}')
            return False
        
# ══════════════════════════════════════════════
# スマホ側とのWebSocket/http通信を行う
# ══════════════════════════════════════════════

def inform_ui_connection_stats():
    """現在の接続状況をUIに通知する"""
    stats = {
        "count": len(CONNECTED_CLIENTS),
        "ips": list(CONNECTED_CLIENTS_INFOS.values())
    }
    # windowオブジェクトを介してJSを実行
    window.evaluate_js(f"connectionStatsUpdate({json.dumps(stats)})")

async def ws_handler(websocket):
    """
    Websocketハンドラ（スマホ側との通信）
    送信・受信メッセージはjson形式で必ずtype属性を含む必要があります。
    """
    # クライアント情報（IPアドレスとポート番号）を取得
    client_ip, client_port = websocket.remote_address

    #　認証メッセージ検証（接続直後） 
    TIMEOUT_SEC = 8
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=TIMEOUT_SEC)
        data = json.loads(raw)
    except asyncio.TimeoutError:
        LOGGER.warning(f"認証タイムアウト: {client_ip}")
        await websocket.close(4001, "Unauthorized")
        return
    except json.JSONDecodeError:
        LOGGER.warning(f"認証失敗(JSON不正): {client_ip}")
        await websocket.close(4001, "Unauthorized")
        return
    except websockets.exceptions.ConnectionClosed:
        return
    # -- 認証メッセージの形式を検査 --
    if data.get("type") != "auth":
        LOGGER.warning(f"認証失敗(type不正): {client_ip}")
        await websocket.close(4001, "Unauthorized")
        return
    # -- トークンを検査 --
    token = data.get("token", "")
    if token != ACCESS_TOKEN:
        LOGGER.warning(f"不正アクセス拒否: {client_ip} (token不一致)")
        await websocket.send(json.dumps({"type": "auth", "ok": False}))
        await websocket.close(4001, "Unauthorized")
        return

    # 認証成功    
    await websocket.send(json.dumps({"type": "auth", "ok": True}))
    
    # クライアントを記録
    CONNECTED_CLIENTS.add(websocket)
    CONNECTED_CLIENTS_INFOS[websocket] = f"{client_ip}:{client_port}"
    inform_ui_connection_stats()

    # （初回接続時）基本情報送信
    await websocket.send(json.dumps({"type": "initial_auth_setup", 
                                    "gesture_shortcuts": GESTURE_SHORTCUTS,
                                    "gesture_labels": GESTURE_LABELS_JP,
                                    "vibration_setting": APP_SETTINGS.get("vibration_enabled")
                                    }))
    
    # （随時）クライアントからのメッセージを待機して処理するループ。接続が切れるまで続く。
    try:
        async for message in websocket:# データが来るたびに一回実行するということ。
            # 受信メッセージは全てJSON形式を想定。コマンドの種類は "type" フィールドで判別する。
            try:
                data = json.loads(message)
                if "type" not in data.keys():
                    LOGGER.warning(f'受信メッセージにtypeが含まれません: {data}')
                    await websocket.send(json.dumps({"type": "error", "ok": False, "error": "invalid json"}))
                    continue
            except json.JSONDecodeError as e:
                LOGGER.warning(f'受信メッセージのJSONが不正です: {e}')
                await websocket.send(json.dumps({"type": "error", "ok": False, "error": "invalid json"}))
                continue
            # 
            msg_type = data.get("type")
            # スマホ側の振動対応状況を受け取る
            if msg_type == "vibration_status":
                supported = bool(data.get("supported", False))
                allowed = bool(data.get("allowed", False))
                if not supported or not allowed:
                    LOGGER.warning(f"端末振動警告: {client_ip} (supported={supported}, allowed={allowed})")
                else:
                    LOGGER.info(f"端末振動状態: {client_ip} 利用可能")
                continue          
            # アプリの設定値を更新する（例: vibration_enabled）
            elif msg_type == "update_setting":
                key = data.get("key")
                value = data.get("value")
                if key in APP_SETTINGS and isinstance(value, bool):
                    APP_SETTINGS[key] = value
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": True, "key": key, "value": value}))
                else:
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": False}))
                continue
            # ジェスチャーを実行する
            elif msg_type == "gesture_detected":
                gesture_name = data.get("gesture_name")
                if not gesture_name or gesture_name not in GESTURE_SHORTCUTS:    # ジェスチャー名が不正 
                    await websocket.send(json.dumps({"type": "error", "ok": False, "error": "no gesture"}))
                    continue
                # ジェスチャーに対応するキー配列を取得。形式が不正ならエラーを返す
                keys = GESTURE_SHORTCUTS[gesture_name]
                if not isinstance(keys, list) or not keys:
                    await websocket.send(json.dumps({"type": "error", "ok": False, "error": "invalid gesture"}))
                    continue
                # キー実行。エラーが出たらクライアントに返す（例: 無効なキー指定）
                try:
                    execute_keys(keys)
                    gesture_label = GESTURE_LABELS_JP.get(gesture_name)
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
        # クライアントを記録から削除
        CONNECTED_CLIENTS.discard(websocket)
        CONNECTED_CLIENTS_INFOS.pop(websocket, None)
        LOGGER.info(f"デバイスが切断されました: {client_ip}")
        inform_ui_connection_stats()

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

# HTTPサーバー
def run_http():
    server = HTTPServer((HOST, HTTP_PORT), SimpleHTTPRequestHandler)
    server.serve_forever()

# ウィンドウが表示された後に実行される関数
def initialize_servers(window):
    threading.Thread(target=run_ws, daemon=True).start()
    threading.Thread(target=run_http, daemon=True).start()
    LOGGER.info("各サーバーを開始しました")

# ══════════════════════════════════════════════
# メイン
# ══════════════════════════════════════════════
if __name__ == '__main__':
    logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    )
    
    api = JSApi()
    
    window = webview.create_window(
       'LeftPad Server', 
        url='server.html', 
        js_api=api,
        width=1100, height=750,
        maximized=True,
        fullscreen=False
    )

    # ログの設定（ここを追加）
    handler = WebviewLogHandler(window)
    # %(asctime)s を使い、時刻の形式は datefmt で指定するのが正解です
    handler.setFormatter(logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S'))
    LOGGER.addHandler(handler)

    # debug=Trueにするとhtmlの調査機能が使える。
    webview.start(initialize_servers, window, debug=False)
