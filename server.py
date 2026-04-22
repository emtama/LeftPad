"""
LeftPad Server  ─  PC側サーバー（QRコード対応版）
=================================================
必要なライブラリのインストール:
  pip install websockets pyautogui qrcode[pil] Pillow

起動方法:
  python server.py

起動するとQRコードウィンドウが表示される。
スマホのカメラアプリでQRを読み込むだけでPWAにアクセスできる。

ポート構成:
  8080  … HTTP（smartphone.html の配信）
  8765  … WebSocket（ボタン操作の受信）
"""

import asyncio
import json
import logging
import os
import socket
import sys
import threading
import tkinter as tk
from http.server import HTTPServer, SimpleHTTPRequestHandler
from io import BytesIO
import secrets

import pyautogui
import qrcode
import websockets
from PIL import Image, ImageTk

# ══════════════════════════════════════════════
#  設定
# ══════════════════════════════════════════════
HOST           = "0.0.0.0"
WS_PORT        = 8765   # WebSocket
HTTP_PORT      = 8080   # smartphone.html 配信
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
SHORTCUTS_FILE = os.path.join(BASE_DIR, "shortcuts.json")
ACCESS_TOKEN = secrets.token_urlsafe(16)

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
#  接続クライアント数（GUIで表示）
# ══════════════════════════════════════════════
connected_clients: set = set()

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
#  ショートカット
# ══════════════════════════════════════════════
def load_shortcuts() -> dict:
    try:
        with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info(f"shortcuts.json 読み込み完了 ({len(data)} 件)")
        return data
    except FileNotFoundError:
        log.error(f"shortcuts.json が見つからない: {SHORTCUTS_FILE}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"shortcuts.json の JSON が不正: {e}")
        sys.exit(1)

def execute_keys(keys: list[str]) -> None:
    pyautogui.PAUSE = 0.02
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys)

def parse_raw_cmd(cmd_str: str) -> list[str]:
    return [k.strip().lower() for k in cmd_str.split("+")]

# ══════════════════════════════════════════════
#  WebSocket ハンドラ
# ══════════════════════════════════════════════
async def ws_handler(websocket):
    client = websocket.remote_address
    log.info(f"WS 接続: {client[0]}:{client[1]}")
    connected_clients.add(websocket)

    shortcuts = load_shortcuts()

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"ok": False, "error": "invalid json"}))
                continue

            cmd    = data.get("cmd", "").strip()
            is_raw = data.get("raw", False)

            if not cmd:
                await websocket.send(json.dumps({"ok": False, "error": "no cmd"}))
                continue

            if is_raw:
                keys = parse_raw_cmd(cmd)
                try:
                    execute_keys(keys)
                    log.info(f"[raw] {cmd} → {keys}")
                    await websocket.send(json.dumps({"ok": True, "cmd": cmd, "keys": keys}))
                except Exception as e:
                    log.error(f"キー実行エラー: {e}")
                    await websocket.send(json.dumps({"ok": False, "error": str(e)}))
                continue

            if cmd not in shortcuts:
                log.warning(f"未定義コマンド: {cmd!r}")
                await websocket.send(json.dumps({"ok": False, "error": f"unknown cmd: {cmd}"}))
                continue

            keys = shortcuts[cmd]
            try:
                execute_keys(keys)
                log.info(f"[cmd] {cmd:20s} → {'+'.join(keys)}")
                await websocket.send(json.dumps({"ok": True, "cmd": cmd, "keys": keys}))
            except Exception as e:
                log.error(f"キー実行エラー: {e}")
                await websocket.send(json.dumps({"ok": False, "error": str(e)}))

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError as e:
        log.warning(f"WS 異常切断: {e}")
    finally:
        connected_clients.discard(websocket)
        log.info(f"WS 切断: {client[0]}")

# ══════════════════════════════════════════════
#  HTTP サーバー（smartphone.html を配信）
# ══════════════════════════════════════════════
class QuietHTTPHandler(SimpleHTTPRequestHandler):
    """アクセスログを抑制したHTTPハンドラ"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        # 200/304 以外のみ表示
        if args and str(args[1]) not in ("200", "304"):
            log.warning(f"HTTP {args[1]} – {args[0]}")

def run_http_server():
    server = HTTPServer((HOST, HTTP_PORT), QuietHTTPHandler)
    log.info(f"HTTP サーバー起動: ポート {HTTP_PORT}")
    server.serve_forever()

# ══════════════════════════════════════════════
#  asyncio ループ（別スレッドで実行）
# ══════════════════════════════════════════════
def run_async_loop():
    pyautogui.FAILSAFE = False

    async def _start():
        async with websockets.serve(ws_handler, HOST, WS_PORT):
            log.info(f"WebSocket サーバー起動: ポート {WS_PORT}")
            await asyncio.Future()

    asyncio.run(_start())

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

    img = qr.make_image(
        fill_color="#0d0e11",   # QRの黒部分
        back_color="#e8ff47",   # QRの白部分（アクセントカラー）
    ).convert("RGB")

    img = img.resize((size, size), Image.NEAREST)
    return ImageTk.PhotoImage(img)

# ══════════════════════════════════════════════
#  tkinter GUI
# ══════════════════════════════════════════════
class QRWindow:
    # カラーパレット（smartphone.html と統一）
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
        self.root.resizable(False, False)

        self._build_ui()
        self._start_status_update()

    # ── UI構築 ────────────────────────────────────────────
    def _build_ui(self):
        root = self.root
        PAD  = 24

        # ロゴ
        tk.Label(
            root, text="◀  LEFTPAD",
            bg=self.BG, fg=self.ACCENT,
            font=("Courier New", 22, "bold"),
            pady=16,
        ).pack()

        # QR フレーム（黄色の枠でスキャンしやすく）
        qr_outer = tk.Frame(root, bg=self.ACCENT, padx=10, pady=10)
        qr_outer.pack(padx=PAD)

        self.qr_photo = make_qr_image(self.http_url, size=260)
        tk.Label(qr_outer, image=self.qr_photo, bg=self.ACCENT).pack()

        # スキャン説明
        tk.Label(
            root,
            text="スマホのカメラでQRコードを読み込む",
            bg=self.BG, fg=self.MUTED,
            font=("Courier New", 9),
            pady=10,
        ).pack()

        # 区切り線
        tk.Frame(root, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # URL情報パネル
        info = tk.Frame(root, bg=self.SURFACE, padx=18, pady=14)
        info.pack(fill="x", padx=PAD, pady=10)

        self._url_row(info, "PWA",        self.http_url, self.ACCENT)
        self._url_row(info, "WebSocket",  self.ws_url,   self.ACCENT2)

        # 区切り線
        tk.Frame(root, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # ステータス行
        st = tk.Frame(root, bg=self.BG, padx=PAD, pady=10)
        st.pack(fill="x")

        tk.Label(
            st, text="接続中のデバイス : ",
            bg=self.BG, fg=self.MUTED,
            font=("Courier New", 10),
        ).pack(side="left")

        self.client_count_var = tk.StringVar(value="0")
        tk.Label(
            st, textvariable=self.client_count_var,
            bg=self.BG, fg=self.ACCENT2,
            font=("Courier New", 14, "bold"),
        ).pack(side="left")

        # ステータスランプ
        self.lamp = tk.Canvas(
            st, width=14, height=14,
            bg=self.BG, highlightthickness=0,
        )
        self.lamp.pack(side="left", padx=(8, 0))
        self.lamp_circle = self.lamp.create_oval(2, 2, 12, 12, fill=self.MUTED, outline="")

        # フッター
        tk.Label(
            root,
            text="ウィンドウを閉じるとサーバーが停止する",
            bg=self.BG, fg=self.BORDER,
            font=("Courier New", 7),
            pady=10,
        ).pack()

        # ウィンドウを画面中央へ
        root.update_idletasks()
        w = root.winfo_reqwidth()
        h = root.winfo_reqheight()
        x = (root.winfo_screenwidth()  - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")

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
            font=("Courier New", 11, "bold"), anchor="w",
        ).pack(side="left", padx=(4, 8))

        # コピーボタン
        tk.Button(
            row, text="copy",
            bg=self.BORDER, fg=self.TEXT,
            font=("Courier New", 8),
            relief="flat", padx=6, pady=1,
            cursor="hand2",
            activebackground=self.ACCENT2,
            activeforeground=self.BG,
            command=lambda v=value: self._copy(v),
        ).pack(side="right")

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        log.info(f"クリップボードにコピー: {text}")

    # ── ステータス更新（1秒ごと）───────────────────────────
    def _start_status_update(self):
        def update():
            n = len(connected_clients)
            self.client_count_var.set(str(n))
            # ランプの色を更新
            lamp_color = self.ACCENT2 if n > 0 else self.MUTED
            self.lamp.itemconfig(self.lamp_circle, fill=lamp_color)
            self.root.after(1000, update)
        self.root.after(500, update)

    def _on_close(self):
        log.info("ウィンドウを閉じた。サーバーを停止する")
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
    ws_url   = f"ws://{ip}:{WS_PORT}?token={ACCESS_TOKEN}"
    
    print("=" * 54)
    print("  LeftPad Server  起動中...")
    print("=" * 54)
    print(f"  PWA (HTTP)  : {http_url}")
    print(f"  WebSocket   : {ws_url}")
    print("  QRコードウィンドウが表示される")
    print("=" * 54)

    # HTTP サーバー（別スレッド）
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # WebSocket / asyncio ループ（別スレッド）
    ws_thread = threading.Thread(target=run_async_loop, daemon=True)
    ws_thread.start()

    # QRウィンドウ（メインスレッド ─ tkinter の制約により必ずメインで実行）
    window = QRWindow(ip=ip, http_url=http_url, ws_url=ws_url)
    window.run()

if __name__ == "__main__":
    main()
