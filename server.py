"""
LeftPad Server  ─  PC側サーバー
==========================================必要なライブラリのインストール:
  pip install websockets pyautogui qrcode[pil] Pillow

起動方法:
  python server.py

ポート構成:
  8080  … HTTP（smartphone.html の配信）
  8765  … WebSocket（ボタン操作の受信）

セキュリティ:
  起動ごとにランダムトークンを生成。QRコードに埋め込まれており、
  WebSocket接続直後の認証メッセージで検証する。
"""

import asyncio
import json
import logging
import os
import queue
import secrets
import socket
import sys
import threading
import tkinter as tk
import tkinter.messagebox as messagebox
from http.server import HTTPServer, SimpleHTTPRequestHandler

import pyautogui
import qrcode
import websockets
from PIL import Image, ImageTk

# ══════════════════════════════════════════════
#  設定
# ══════════════════════════════════════════════
HOST           = "0.0.0.0"
WS_PORT        = 8765
HTTP_PORT      = 8080
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
GESTURE_LABELS_FILE = os.path.join(BASE_DIR, "gesture_labels.json")
GESTURES_FILE  = os.path.join(BASE_DIR, "gesture_shortcuts.json")
ACCESS_TOKEN   = secrets.token_urlsafe(16)   # 起動ごとに再生成

WS_BROADCAST_QUEUE = queue.Queue()  # WebSocketブロードキャスト用のキュー。UIスレッドからこのキューにメッセージを入れると、broadcaster()が全クライアントに送信する。

COMBO_CONNECTOR_STRING:str = " + "  # ジェスチャーのキーコンボを結合する文字列（例: "Ctrl + Shift + a"）

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


# ══════════════════════════════════════════════
#  ログ設定
# ══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("leftpad")

# ══════════════════════════════════════════════
#  接続クライアント
# ══════════════════════════════════════════════
connected_clients: set = set()
connected_client_infos: dict = {}
APP_SETTINGS = {
    "vibration_enabled": True,
}

# ══════════════════════════════════════════════
#  ネットワーク
# ══════════════════════════════════════════════
def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# ══════════════════════════════════════════════
#  ショートカット I/O
# ══════════════════════════════════════════════

# ジェスチャーと日本語ラベルの対応を読み込む。存在しない場合は空の辞書を返す。形式が不正な場合も空の辞書を返す。
def load_gesture_labels() -> dict:
    """gesture_labels.json を読み込む（ジェスチャー: 日本語ラベルの形式）"""
    try:
        with open(GESTURE_LABELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("gesture_labels.json must be object")
        return data
    except Exception as e:
        log.error(f"gesture_labels.json の読み込みに失敗: {e}")
        return {}
    
# ジェスチャーと対応キーの一覧を読み込む。存在しない場合は空の辞書を返す。形式が不正な場合も空の辞書を返す。
def load_gestures() -> dict:
    """gesture_shortcuts.json を読み込む（ジェスチャー: キー配列の形式）"""
    if not os.path.exists(GESTURES_FILE):
        return {}
    try:
        with open(GESTURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("gesture_shortcuts.json must be object")
        # もしもgesture_labels.jsonにないキーがあった場合、対応できないので削除、警告
        for key in list(data.keys()):
            if key not in GESTURE_LABELS_JP:
                log.warning(f"不明なジェスチャーがショートカットに存在したので削除しました: {key}")
                del data[key]
        return data
    except Exception as e:
        log.error(f"gesture_shortcuts.json の読み込みに失敗: {e}")
        return {}
    
# ジェスチャーと対応キーの一覧を保存
def save_gestures(data: dict) -> bool:
    try:
        with open(GESTURES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("gesture_shortcuts.json を保存した")
        return True
    except Exception as e:
        log.error(f"gesture_shortcuts.json の保存に失敗: {e}")
        return False


# ══════════════════════════════════════════════
#  キー入力
# ══════════════════════════════════════════════
def execute_keys(keys: list[str]) -> None:
    pyautogui.PAUSE = 0.02
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)

# ══════════════════════════════════════════════
#  WebSocket ハンドラ
# ══════════════════════════════════════════════
# WebSocket通信での送信データは、JSON形式で、"type"フィールドでコマンドの種類を識別する。例: {"type": "auth", "token": "xxxx"}（認証メッセージ）や {"type": "gesture", "gesture": "swipe_up"}（ジェスチャーコマンド）など。
async def ws_handler(websocket):
    # クライアント情報（IPアドレスとポート番号）を取得
    client = websocket.remote_address

    # ── 認証メッセージ検証（接続直後） ─────────────
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=8)
        data = json.loads(raw)
    except asyncio.TimeoutError:
        log.warning(f"認証タイムアウト: {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    except json.JSONDecodeError:
        log.warning(f"認証失敗(JSON不正): {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    except websockets.exceptions.ConnectionClosed:
        return
    # -- 認証メッセージの形式を検査 --
    if data.get("type") != "auth":
        log.warning(f"認証失敗(type不正): {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return
    # -- トークンを検査 --
    token = data.get("token", "")
    if token != ACCESS_TOKEN:
        log.warning(f"不正アクセス拒否: {client[0]} (token不一致)")
        await websocket.send(json.dumps({"type": "auth", "ok": False}))
        await websocket.close(4001, "Unauthorized")
        return

    await websocket.send(json.dumps({"type": "auth", "ok": True}))
    
    # ── 認証成功 ─────────────
    log.info(f"WS 接続: {client[0]}:{client[1]}")
    connected_clients.add(websocket)
    connected_client_infos[websocket] = f"{client[0]}:{client[1]}"

    # （初回接続時）基本情報送信
    gestures = load_gestures()
    await websocket.send(json.dumps({"type": "gestures", "data": gestures}))
    await websocket.send(json.dumps({"type": "gesture_labels", "data": GESTURE_LABELS_JP}))
    await websocket.send(json.dumps({"type": "vibration_setting", "data": APP_SETTINGS.get("vibration_enabled")}))
    
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
            # "vibration_status" → 端末のバイブレーション対応状況を受け取る
            if msg_type == "vibration_status":
                supported = bool(data.get("supported", False))
                allowed = bool(data.get("allowed", False))
                if not supported or not allowed:
                    log.warning(f"端末バイブレーション警告: {client[0]} (supported={supported}, allowed={allowed})")
                else:
                    log.info(f"端末バイブレーション状態: {client[0]} 利用可能")
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
                log.info(f"[ジェスチャー:{gesture_label}] {gesture_name:30s} → {'+'.join(keys)}")
                await websocket.send(json.dumps({"type": "gesture_result", "ok": True, "gesture": gesture_name, "keys": keys}))
            except Exception as e:
                log.error(f"キー実行エラー: {e}")
                await websocket.send(json.dumps({"type": "gesture_result", "ok": False, "error": str(e)}))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        log.warning(f"WS 異常切断: {e}")
    finally:
        connected_clients.discard(websocket)
        connected_client_infos.pop(websocket, None)
        log.info(f"WS 切断: {client[0]}")

# ══════════════════════════════════════════════
#  HTTP サーバー
# ══════════════════════════════════════════════
class QuietHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        if args and str(args[1]) not in ("200", "304"):
            log.warning(f"HTTP {args[1]} – {args[0]}")

def run_http_server():
    server = HTTPServer((HOST, HTTP_PORT), QuietHTTPHandler)
    log.info(f"HTTP サーバー起動: ポート {HTTP_PORT}")
    server.serve_forever()

# ══════════════════════════════════════════════
#  asyncio ループ
# ══════════════════════════════════════════════
def run_async_loop():
    # PyAutoGUIのフェイルセーフを有効化
    # マウスを画面左上(0,0)に移動すると強制停止する安全機構
    pyautogui.FAILSAFE = True
    # WebSocketブロードキャスト用のキュー。UIスレッドからこのキューにメッセージを入れると、broadcaster()が全クライアントに送信する。
    async def broadcaster():
        while True:
            msg = await asyncio.to_thread(WS_BROADCAST_QUEUE.get)
            dead = []
            for client in connected_clients:
                try:
                    await client.send(msg)
                except Exception:
                    dead.append(client)
            for d in dead:
                connected_clients.discard(d)
    # 非同期処理のメイン関数（WebSocketサーバーの起動と維持）
    async def _start():
        # WebSocketサーバーを起動
        # ws_handler: 接続ごとの処理関数
        # HOST, WS_PORT: バインド先アドレスとポート
        async with websockets.serve(ws_handler, HOST, WS_PORT):
            # サーバー起動ログ
            log.info(f"WebSocket サーバー起動: ポート {WS_PORT}")
            # ブロードキャストタスクを開始
            task = asyncio.create_task(broadcaster())
            # 永久に待機してサーバーを終了させない
            # Future()は未完了のままなので、ここでイベントループを維持する
            await asyncio.Future()

    # asyncioイベントループを開始し、_start()を実行
    # この関数が終了するまで（=通常は終了しない）ループが継続する
    asyncio.run(_start())

def broadcast_message(type: str, data: any) -> None:
    msg = json.dumps({
        "type": type,
        "data": data
    }, ensure_ascii=False)
    WS_BROADCAST_QUEUE.put(msg)
# ══════════════════════════════════════════════
#  QRコード生成
# ══════════════════════════════════════════════
def make_qr_image(url: str, size: int = 260) -> ImageTk.PhotoImage:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#0d0e11", back_color="#e8ff47").convert("RGB")
    img = img.resize((size, size), Image.NEAREST)
    return ImageTk.PhotoImage(img)

# ══════════════════════════════════════════════
#  ジェスチャーショートカット編集ウィンドウ
# ══════════════════════════════════════════════
class InlineGestureEditor(tk.Frame):
    BG      = "#0d0e11"
    SURFACE = "#3e424f"
    BORDER  = "#2a2d36"
    ACCENT  = "#e8ff47"
    ACCENT2 = "#47c4ff"
    MUTED   = "#5a5f72"
    TEXT    = "#e4e6ee"
    DANGER  = "#ff5c5c"
    MODIFIERS = {"Ctrl", "Shift", "Alt", "Meta"}

    def __init__(self, parent):
        super().__init__(parent, bg=self.SURFACE, padx=8, pady=8)
        self._gestures = load_gestures()
        self._gesture_vars = {}
        self._capture_buttons = {}
        self._capture_target = None
        self._capture_pressed = set()
        self._capture_candidate = []
        self._build()
        self.bind_all("<KeyPress>", self._on_key_press, add="+")
        self.bind_all("<KeyRelease>", self._on_key_release, add="+")

    # UI構築。ジェスチャーごとに行を作り、ラベル・エントリー・記録ボタン・削除ボタンを配置。エントリーは現在の割り当てを表示し、変更を保存するためのもの。記録ボタンは押すとキーキャプチャモードになり、押されたキーをリアルタイムでエントリーに反映。削除ボタンは割り当てを消す。
    def _build(self):
        # タイトル
        tk.Label(self, text="ジェスチャー割り当て", bg=self.SURFACE, fg=self.ACCENT2, font=("Courier New", 14, "bold")).pack(anchor="w")
        
        body = tk.Frame(self, bg=self.SURFACE)
        body.pack(fill="both", expand=True, pady=(6, 0))
        
        canvas = tk.Canvas(body, bg=self.SURFACE, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)        
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)        
        scrollbar.pack(side="right", fill="y")

        # マウスホイールスクロール対応
        # Canvasスクロール有効領域フラグ
        self._canvas_hover = False
        canvas.bind("<Enter>", lambda e: self._set_canvas_hover(True))
        canvas.bind("<Leave>", lambda e: self._set_canvas_hover(False))
        canvas.bind_all("<MouseWheel>", lambda e: self._on_mousewheel(e, canvas))
        canvas.bind_all("<Button-4>", lambda e: self._on_mousewheel_linux(e, canvas))
        canvas.bind_all("<Button-5>", lambda e: self._on_mousewheel_linux(e, canvas))

        box = tk.Frame(canvas, bg=self.SURFACE)
        win = canvas.create_window((0, 0), window=box, anchor="nw")
        box.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        for key in GESTURE_KEYS:
            row = tk.Frame(box, bg=self.SURFACE, pady=2)
            row.pack(fill="x")
            tk.Label(row, text=GESTURE_LABELS_JP.get(key, key), width=16, anchor="w", bg=self.SURFACE, fg=self.TEXT, font=("Courier New", 10)).pack(side="left")
            cmd = self._gestures.get(key, "")
            combo = COMBO_CONNECTOR_STRING.join(cmd) if isinstance(cmd, list) else str(cmd)
            var = tk.StringVar(value=combo)

            ent = tk.Entry(row, textvariable=var, width=18, bg=self.BG, fg=self.ACCENT2, relief="flat", font=("Courier New", 10))
            ent.pack(side="left", padx=(4, 6))

            btn = tk.Button(row, text="記録", bg=self.BORDER, fg=self.TEXT, relief="flat", font=("Courier New", 9), command=lambda g=key: self._start_capture(g))
            btn.pack(side="left", padx=2)
            
            delbtn = tk.Button(row, text="削除", bg=self.DANGER, fg=self.BG, relief="flat", font=("Courier New", 9), command=lambda g=key: self._delete(g))
            delbtn.pack(side="left", padx=2)

            # エントリーの内容が変更されたときに呼ばれるコールバックを設定。入力されたキーコンボをジェスチャーに保存するためのもの。形式は "Ctrl+Shift+a" → ["Ctrl", "Shift", "a"] のように変換して保存。
            var.trace_add("write", lambda *_args, g=key: self._save_one(g))
            
            self._gesture_vars[key] = var
            self._capture_buttons[key] = btn

    # マウスホイールイベント処理
    def _set_canvas_hover(self, hover: bool):
        self._canvas_hover = hover
    def _on_mousewheel(self, event, canvas):
        # Windows / macOS
        if event.delta:
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    def _on_mousewheel_linux(self, event, canvas):
        # Linux（X11系）
        if event.num == 4:
            canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            canvas.yview_scroll(3, "units")

    # キーシンボルを正規化。大文字を小文字に変換し、左右のモディファイアキーを統一。例: "Control_L" → "Ctrl", "Shift_R" → "Shift", "Return" → "Enter" など。その他のキーは小文字化して返す。
    def _normalize(self, keysym):
        k = keysym.lower()
        return {"control_l":"Ctrl",
                "control_r":"Ctrl",
                "shift_l":"Shift",
                "shift_r":"Shift",
                "alt_l":"Alt",
                "alt_r":"Alt",
                "meta_l":"Meta",
                "meta_r":"Meta",
                "return":"Enter",
                "escape":"Esc"}.get(k, k)
    
    # キャプチャ開始。すでにキャプチャ中なら確定 or キャンセル。別のキーのキャプチャ中なら無視。
    def _start_capture(self, key):
        if self._capture_target and self._capture_target != key:
            return
        if self._capture_target == key:
            self._confirm_capture()
            return
        # キャプチャ開始
        self._capture_target = key  # どのジェスチャーのキーをキャプチャしているか
        self._capture_pressed.clear()   # キャプチャ中に押されたキーのセット
        self._capture_candidate = []    # キャプチャ中のキーセットをモディファイアと通常キーに分けてソートしたもの。UI表示用。
        # UI更新: 対象のキーのボタンを「確定」にして強調、他のボタンは無効化して薄くする
        for k, b in self._capture_buttons.items():
            if k == key:
                b.configure(text="確定", bg=self.ACCENT2, fg=self.BG)
            else:
                b.configure(state="無効", bg=self.MUTED, fg=self.BG)
    # キーイベント処理。キャプチャ中のキーセットを更新して候補表示に反映。キャプチャ対象外のキーは無視。
    def _on_key_press(self, event):
        if not self._capture_target:
            return
        self._capture_pressed.add(self._normalize(event.keysym))
        mods = set()
        if event.state & 0x0001: mods.add("Shift")
        if event.state & 0x0004: mods.add("Ctrl")
        if event.state & 0x0008: mods.add("Alt")
        self._capture_pressed.update(mods)
        self._update_candidate()
    # キーリリースイベント。キャプチャ中のキーセットから離されたキーを削除して候補表示に反映。キャプチャ対象外のキーは無視。
    def _on_key_release(self, _event):
        if self._capture_target:
            pass
    # キャプチャ中のキーセットから、モディファイアと通常キーを分けてソート。候補表示を更新。
    def _update_candidate(self):
        keys = set(self._capture_pressed)
        if not keys:
            return
        modifiers = [k for k in self.MODIFIERS if k in keys]
        non_mods = sorted([k for k in keys if k not in self.MODIFIERS])
        combo = modifiers + non_mods
        if not combo:# キーがない場合は候補表示しない
            return
        self._capture_candidate = combo # キャプチャ中のキーセットをモディファイアと通常キーに分けてソートしたものを保存
        self._gesture_vars[self._capture_target].set(COMBO_CONNECTOR_STRING.join(combo))    # UIのエントリーに反映
    # キャプチャ確定。候補のキーセットをジェスチャーに保存してUIをリセット。候補が空なら保存せずにリセット。
    def _confirm_capture(self):
        # 既存値がある場合に候補未入力でもエラーにしない
        self._capture_target = None
        self._capture_pressed.clear()
        self._capture_candidate = []
        # UIリセット
        for b in self._capture_buttons.values():
            b.configure(text="キーを記録", state="normal", bg=self.BORDER, fg=self.TEXT)        
    # 削除ボタン。対応するジェスチャーのキー割り当てを空にして保存。UIも空にする。
    def _delete(self, key):
        self._gesture_vars[key].set([])
    # エントリーの内容が変更されたときに呼ばれる。入力されたキーコンボをジェスチャーに保存。形式は "Ctrl+Shift+a" → ["Ctrl", "Shift", "a"] のように変換して保存。
    def _save_one(self, gkey):
        combo = self._gesture_vars[gkey].get().strip().lower()
        # 入力が空ならジェスチャーの割り当ても空にする。そうでなければ、入力をCOMBO_CONNECTOR_STRINGで分割してリスト化し、ジェスチャーに保存。保存後、全ジェスチャーをファイルに書き出す。
        self._gestures[gkey] = [k.strip() for k in combo.split(COMBO_CONNECTOR_STRING)] if combo else []
        # 保存
        save_gestures(self._gestures)
        # キャプチャ確定後にスマホ側にデータを送信
        broadcast_message("gestures", self._gestures)


# ══════════════════════════════════════════════
#  QRコードウィンドウ（メインGUI）
# ══════════════════════════════════════════════
class UILogHandler(logging.Handler):
    def __init__(self, log_queue: "queue.Queue[str]"):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            self.log_queue.put_nowait(self.format(record))
        except Exception:
            pass


class QRWindow:
    BG      = "#0d0e11"
    SURFACE = "#16181e"
    BORDER  = "#2a2d36"
    ACCENT  = "#e8ff47"
    ACCENT2 = "#47c4ff"
    MUTED   = "#5a5f72"
    TEXT    = "#e4e6ee"

    def __init__(self, ip: str, http_url: str, ws_url: str):
        self.ip       = ip
        self.http_url = http_url
        self.ws_url   = ws_url

        self.root = tk.Tk()
        self.root.title("LeftPad Server")
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self._log_handler = UILogHandler(self.log_queue)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        )
        log.addHandler(self._log_handler)

        self._build_ui()
        self._start_status_update()
        self._start_log_update()
        self._maximize_window()
    # UI構築。左側にQRコードとURL、接続デバイス情報、設定項目などを配置。右側はジェスチャーショートカット編集ウィンドウ。
    def _build_ui(self):
        root = self.root
        PAD  = 24
        split = tk.Frame(root, bg=self.BG)
        split.pack(fill="both", expand=True)
        left = tk.Frame(split, bg=self.BG)
        right = tk.Frame(split, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
        right.pack(side="left", fill="both", expand=True, padx=(6, 12), pady=12)

        # ロゴ
        tk.Label(
            left, text="LEFTPAD",
            bg=self.BG, fg=self.ACCENT,
            font=("Courier New", 22, "bold"), pady=8,
        ).pack()

        # QRコード
        qr_outer = tk.Frame(left, bg=self.ACCENT, padx=10, pady=10)
        qr_outer.pack(padx=PAD)

        self.qr_photo = make_qr_image(self.http_url, size=260)
        tk.Label(qr_outer, image=self.qr_photo, bg=self.ACCENT).pack()

        tk.Label(
            left, text="スマホのカメラでQRコードを読み込む",
            bg=self.BG, fg=self.MUTED,
            font=("Courier New", 9), pady=10,
        ).pack()

        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # URLパネル
        info = tk.Frame(left, bg=self.SURFACE, padx=18, pady=14)
        info.pack(fill="x", padx=PAD, pady=10)
        self._url_row(info, "PWA",       self.http_url, self.ACCENT)
        self._url_row(info, "WebSocket", self.ws_url,   self.ACCENT2)

        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # ステータス行
        st = tk.Frame(left, bg=self.BG, padx=PAD, pady=10)
        st.pack(fill="x")
        # 接続クライアント数表示
        tk.Label(
            st, text="接続中のデバイス : ",
            bg=self.BG, fg=self.MUTED, font=("Courier New", 10),
        ).pack(side="left")
        # 接続クライアント数表示とランプ
        self.client_count_var = tk.StringVar(value="0")
        tk.Label(
            st, textvariable=self.client_count_var,
            bg=self.BG, fg=self.ACCENT2,
            font=("Courier New", 14, "bold"),
        ).pack(side="left")
        # 接続ランプ（緑が1台以上、灰色が0台）
        self.lamp = tk.Canvas(st, width=14, height=14, bg=self.BG, highlightthickness=0)
        self.lamp.pack(side="left", padx=(8, 0))
        self.lamp_circle = self.lamp.create_oval(2, 2, 12, 12, fill=self.MUTED, outline="")
        
        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # スマホ振動設定
        vib_row = tk.Frame(left, bg=self.BG, padx=PAD, pady=8)
        vib_row.pack(fill="x")
        self.vibration_var = tk.BooleanVar(value=APP_SETTINGS.get("vibration_enabled", True))
        tk.Checkbutton(
            vib_row,
            text="タップ振動",
            variable=self.vibration_var,
            command=self._toggle_vibration,
            bg=self.BG, fg=self.TEXT, selectcolor=self.SURFACE,
            activebackground=self.BG, activeforeground=self.ACCENT2,
            font=("Courier New", 14), highlightthickness=0, bd=0,
            width=40, height=40
        ).pack(anchor="w")

        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # 接続デバイス情報
        device_panel = tk.Frame(left, bg=self.SURFACE, padx=10, pady=8)
        device_panel.pack(fill="x", padx=PAD, pady=(10, 0))
        tk.Label(
            device_panel, text="接続デバイス",
            bg=self.SURFACE, fg=self.MUTED, font=("Courier New", 9, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        self.device_list_var = tk.StringVar(value=("未接続",))
        tk.Listbox(
            device_panel,
            listvariable=self.device_list_var,
            bg=self.BG, fg=self.TEXT, height=3,
            highlightthickness=0, borderwidth=0, font=("Consolas", 9),
        ).pack(fill="x")

        tk.Frame(left, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # ログ表示（コマンドプロンプトを見なくても状態確認できる）
        log_panel = tk.Frame(left, bg=self.SURFACE, padx=10, pady=8)
        log_panel.pack(fill="both", expand=True, padx=PAD, pady=10)
        tk.Label(
            log_panel, text="サーバーログ",
            bg=self.SURFACE, fg=self.MUTED, font=("Courier New", 9, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.log_text = tk.Text(
            log_panel, height=10,
            bg=self.BG, fg=self.TEXT, insertbackground=self.TEXT,
            font=("Consolas", 9), relief="flat", wrap="none",
        )
        log_scroll = tk.Scrollbar(log_panel, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # 右半分: インライン編集
        inline_editor = InlineGestureEditor(right)
        inline_editor.pack(fill="both", expand=True)

        # フッター
        tk.Label(
            left, text="ウィンドウを閉じるとサーバーが停止する",
            bg=self.BG, fg=self.BORDER,
            font=("Courier New", 7), pady=6,
        ).pack()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _url_row(self, parent, label: str, value: str, color: str):
        row = tk.Frame(parent, bg=self.SURFACE)
        row.pack(fill="x", pady=3)

        tk.Label(
            row, text=f"{label:<10}",
            bg=self.SURFACE, fg=self.MUTED,
            font=("Courier New", 9), anchor="w",
        ).pack(side="left")

        tk.Label(
            row, text=value,
            bg=self.SURFACE, fg=color,
            font=("Courier New", 10, "bold"), anchor="w",
        ).pack(side="left", padx=(4, 8))

        tk.Button(
            row, text="copy",
            bg=self.BORDER, fg=self.TEXT,
            font=("Courier New", 8),
            relief="flat", padx=6, pady=1, cursor="hand2",
            activebackground=self.ACCENT2, activeforeground=self.BG,
            command=lambda v=value: self._copy(v),
        ).pack(side="right")
    # クリップボードにテキストをコピーする。URLの横の「copy」ボタンから呼ばれる。引数のテキストをクリップボードにセットする。
    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
    # ウィンドウを最大化する。OSや環境によって最大化の方法が異なるため、複数の方法を試す。まずは標準的なstate("zoomed")を試し、失敗したらattributes("-zoomed")、それもダメなら画面サイズを直接指定してフルスクリーンにする。
    def _maximize_window(self):
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
    # スマホのタップ振動設定が変更されたときの処理。APP_SETTINGSの値を更新してログに出力し、全クライアントに新しい設定値をブロードキャストする。
    def _toggle_vibration(self):
        APP_SETTINGS["vibration_enabled"] = bool(self.vibration_var.get())
        log.info(f"スマホ振動設定: {'ON' if APP_SETTINGS['vibration_enabled'] else 'OFF'}")
        broadcast_message("vibration_setting_updated", APP_SETTINGS["vibration_enabled"])
    # ステータス更新を開始する。接続クライアント数と接続デバイス情報を1秒ごとに更新する。接続クライアント数が0ならランプを灰色、1台以上ならアクセントカラーにする。
    def _start_status_update(self):
        def update():
            n = len(connected_clients)
            self.client_count_var.set(str(n))
            self.lamp.itemconfig(self.lamp_circle,
                                 fill=self.ACCENT2 if n > 0 else self.MUTED)
            infos = sorted(connected_client_infos.values()) if n > 0 else ["未接続"]
            self.device_list_var.set(infos)
            self.root.after(1000, update)
        self.root.after(500, update)

    def _start_log_update(self):
        def update():
            if not self.root.winfo_exists():
                return
            try:
                while True:
                    line = self.log_queue.get_nowait()
                    self.log_text.insert("end", line + "\n")
                    self.log_text.see("end")
            except queue.Empty:
                pass
            self.root.after(120, update)
        self.root.after(120, update)

    def _on_close(self):
        log.info("ウィンドウを閉じた。サーバーを停止する")
        log.removeHandler(self._log_handler)
        self.root.destroy()
        os._exit(0)

    def run(self):
        self.root.mainloop()

# ══════════════════════════════════════════════
#  エントリーポイント
# ══════════════════════════════════════════════
def main():
    ip       = get_local_ip()
    http_url = f"http://{ip}:{HTTP_PORT}/smartphone.html?token={ACCESS_TOKEN}"
    ws_url   = f"ws://{ip}:{WS_PORT}"

    print("=" * 56)
    print("  LeftPad Server  起動中...")
    print("=" * 56)
    print(f"  PWA (HTTP)  : {http_url}")
    print(f"  WebSocket   : {ws_url}")
    print("=" * 56)

    threading.Thread(target=run_http_server, daemon=True).start()
    threading.Thread(target=run_async_loop,  daemon=True).start()

    QRWindow(ip=ip, http_url=http_url, ws_url=ws_url).run()

if __name__ == "__main__":
    main()
