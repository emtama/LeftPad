"""
LeftPad Server  ─  PC側サーバー
=================================================
必要なライブラリのインストール:
  pip install websockets pyautogui qrcode[pil] Pillow

起動方法:
  python server.py

ポート構成:
  8080  … HTTP（smartphone.html の配信）
  8765  … WebSocket（ボタン操作の受信）

セキュリティ:
  起動ごとにランダムトークンを生成。QRコードに埋め込まれており、
  QR経由でアクセスしたクライアントのみWebSocket接続を許可する。
"""

import asyncio
import json
import logging
import os
import secrets
import socket
import sys
import threading
import tkinter as tk
import tkinter.messagebox as messagebox
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

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
SHORTCUTS_FILE = os.path.join(BASE_DIR, "shortcuts.json")
ACCESS_TOKEN   = secrets.token_urlsafe(16)   # 起動ごとに再生成

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
def load_shortcuts() -> dict:
    try:
        with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        log.error(f"shortcuts.json が見つからない: {SHORTCUTS_FILE}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        log.error(f"shortcuts.json の JSON が不正: {e}")
        sys.exit(1)

def save_shortcuts(data: dict) -> bool:
    try:
        with open(SHORTCUTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("shortcuts.json を保存した")
        return True
    except Exception as e:
        log.error(f"shortcuts.json の保存に失敗: {e}")
        return False

def get_real_shortcuts(shortcuts: dict) -> dict:
    """コメントキーを除いた実際のショートカットのみを返す"""
    return {k: v for k, v in shortcuts.items()
            if isinstance(v, list) and not k.startswith("_")}

# ══════════════════════════════════════════════
#  キー入力
# ══════════════════════════════════════════════
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

    # ── トークン検証 ──────────────────────────────
    try:
        raw_path = websocket.path          # websockets >= 10
    except AttributeError:
        raw_path = getattr(websocket, 'request_uri', "/")

    params = parse_qs(urlparse(raw_path).query)
    token  = params.get("token", [""])[0]

    if token != ACCESS_TOKEN:
        log.warning(f"不正アクセス拒否: {client[0]} (token不一致)")
        await websocket.close(4001, "Unauthorized")
        return

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

            msg_type = data.get("type", "")   # "get_shortcuts" / "update_shortcut" / ""

            # ── ショートカット一覧を返す ─────────────
            if msg_type == "get_shortcuts":
                real = get_real_shortcuts(shortcuts)
                await websocket.send(json.dumps({"type": "shortcuts", "data": real}))
                continue

            # ── ショートカットを更新・保存 ────────────
            if msg_type == "update_shortcut":
                cmd  = data.get("cmd", "").strip()
                keys = data.get("keys")
                if not cmd or not isinstance(keys, list) or not keys:
                    await websocket.send(json.dumps({"type": "shortcut_updated", "ok": False}))
                    continue
                shortcuts[cmd] = keys
                ok = save_shortcuts(shortcuts)
                log.info(f"ショートカット更新: {cmd} → {'+'.join(keys)}")
                await websocket.send(json.dumps({
                    "type": "shortcut_updated", "ok": ok, "cmd": cmd, "keys": keys
                }))
                continue

            # ── 既存のキー入力コマンド ────────────────
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

            real = get_real_shortcuts(shortcuts)
            if cmd not in real:
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
    img = qr.make_image(fill_color="#0d0e11", back_color="#e8ff47").convert("RGB")
    img = img.resize((size, size), Image.NEAREST)
    return ImageTk.PhotoImage(img)

# ══════════════════════════════════════════════
#  ショートカット編集ウィンドウ
# ══════════════════════════════════════════════
class ShortcutEditorWindow(tk.Toplevel):
    BG      = "#0d0e11"
    SURFACE = "#16181e"
    BORDER  = "#2a2d36"
    ACCENT  = "#e8ff47"
    ACCENT2 = "#47c4ff"
    MUTED   = "#5a5f72"
    TEXT    = "#e4e6ee"
    DANGER  = "#ff5c5c"

    def __init__(self, parent_root):
        super().__init__(parent_root)
        self.title("LeftPad ─ ショートカット編集")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(480, 400)

        self._shortcuts = load_shortcuts()
        self._entries   = {}   # cmd → StringVar

        self._build()
        self._center()
        self.focus_force()

    # ── UI ───────────────────────────────────
    def _build(self):
        # タイトル
        tk.Label(
            self, text="ショートカット編集",
            bg=self.BG, fg=self.ACCENT,
            font=("Courier New", 14, "bold"), pady=12,
        ).pack()

        tk.Label(
            self,
            text="キーは  ctrl+z  のように  +  で区切って入力する",
            bg=self.BG, fg=self.MUTED, font=("Courier New", 9),
        ).pack(pady=(0, 8))

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", padx=16)

        # ── スクロール可能なリスト ──
        wrapper = tk.Frame(self, bg=self.BG)
        wrapper.pack(fill="both", expand=True, padx=16, pady=8)

        canvas    = tk.Canvas(wrapper, bg=self.BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(wrapper, orient="vertical", command=canvas.yview,
                                 bg=self.BORDER, troughcolor=self.SURFACE)
        scroll_frame = tk.Frame(canvas, bg=self.BG)

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # マウスホイールスクロール
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"))

        # ── ヘッダー行 ──
        hdr = tk.Frame(scroll_frame, bg=self.SURFACE, padx=4, pady=4)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="コマンド", width=22, bg=self.SURFACE,
                 fg=self.MUTED, font=("Courier New", 9, "bold"), anchor="w").grid(row=0, column=0, padx=(4, 8))
        tk.Label(hdr, text="割り当てキー", width=26, bg=self.SURFACE,
                 fg=self.MUTED, font=("Courier New", 9, "bold"), anchor="w").grid(row=0, column=1)

        # ── ショートカット行 ──
        real = get_real_shortcuts(self._shortcuts)
        for i, (cmd, keys) in enumerate(real.items()):
            row_bg = self.BG if i % 2 == 0 else self.SURFACE
            row = tk.Frame(scroll_frame, bg=row_bg, pady=3)
            row.pack(fill="x")

            tk.Label(
                row, text=cmd, width=22,
                bg=row_bg, fg=self.TEXT,
                font=("Courier New", 9), anchor="w",
            ).grid(row=0, column=0, padx=(4, 8))

            var = tk.StringVar(value="+".join(keys))
            entry = tk.Entry(
                row, textvariable=var, width=26,
                bg=self.SURFACE, fg=self.ACCENT2,
                font=("Courier New", 10),
                relief="flat", bd=1,
                insertbackground=self.ACCENT2,
                highlightthickness=1,
                highlightbackground=self.BORDER,
                highlightcolor=self.ACCENT2,
            )
            entry.grid(row=0, column=1, padx=4)
            self._entries[cmd] = var

        # ── フッター ──
        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", padx=16)

        footer = tk.Frame(self, bg=self.BG, pady=12)
        footer.pack()

        tk.Button(
            footer, text="  保存  ",
            bg=self.ACCENT, fg=self.BG,
            font=("Courier New", 11, "bold"),
            relief="flat", padx=16, pady=6,
            cursor="hand2",
            activebackground="#c8e030",
            command=self._save,
        ).pack(side="left", padx=8)

        tk.Button(
            footer, text="キャンセル",
            bg=self.BORDER, fg=self.TEXT,
            font=("Courier New", 10),
            relief="flat", padx=12, pady=6,
            cursor="hand2",
            command=self.destroy,
        ).pack(side="left", padx=8)

    def _save(self):
        changed = 0
        for cmd, var in self._entries.items():
            raw = var.get().strip()
            if not raw:
                continue
            new_keys = [k.strip().lower() for k in raw.split("+") if k.strip()]
            if new_keys and self._shortcuts.get(cmd) != new_keys:
                self._shortcuts[cmd] = new_keys
                changed += 1

        if changed == 0:
            messagebox.showinfo("LeftPad", "変更なし", parent=self)
            return

        if save_shortcuts(self._shortcuts):
            messagebox.showinfo("LeftPad", f"{changed} 件のショートカットを保存した", parent=self)
            self.destroy()
        else:
            messagebox.showerror("LeftPad", "保存に失敗した", parent=self)

    def _center(self):
        self.update_idletasks()
        w, h = 520, 560
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

# ══════════════════════════════════════════════
#  QRコードウィンドウ（メインGUI）
# ══════════════════════════════════════════════
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
        self.root.resizable(False, False)

        self._build_ui()
        self._start_status_update()

    def _build_ui(self):
        root = self.root
        PAD  = 24

        # ロゴ
        tk.Label(
            root, text="◀  LEFTPAD",
            bg=self.BG, fg=self.ACCENT,
            font=("Courier New", 22, "bold"), pady=16,
        ).pack()

        # QRコード
        qr_outer = tk.Frame(root, bg=self.ACCENT, padx=10, pady=10)
        qr_outer.pack(padx=PAD)

        self.qr_photo = make_qr_image(self.http_url, size=260)
        tk.Label(qr_outer, image=self.qr_photo, bg=self.ACCENT).pack()

        tk.Label(
            root, text="スマホのカメラでQRコードを読み込む",
            bg=self.BG, fg=self.MUTED,
            font=("Courier New", 9), pady=10,
        ).pack()

        tk.Frame(root, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # URLパネル
        info = tk.Frame(root, bg=self.SURFACE, padx=18, pady=14)
        info.pack(fill="x", padx=PAD, pady=10)
        self._url_row(info, "PWA",       self.http_url, self.ACCENT)
        self._url_row(info, "WebSocket", self.ws_url,   self.ACCENT2)

        tk.Frame(root, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # ステータス行
        st = tk.Frame(root, bg=self.BG, padx=PAD, pady=10)
        st.pack(fill="x")

        tk.Label(
            st, text="接続中のデバイス : ",
            bg=self.BG, fg=self.MUTED, font=("Courier New", 10),
        ).pack(side="left")

        self.client_count_var = tk.StringVar(value="0")
        tk.Label(
            st, textvariable=self.client_count_var,
            bg=self.BG, fg=self.ACCENT2,
            font=("Courier New", 14, "bold"),
        ).pack(side="left")

        self.lamp = tk.Canvas(st, width=14, height=14, bg=self.BG, highlightthickness=0)
        self.lamp.pack(side="left", padx=(8, 0))
        self.lamp_circle = self.lamp.create_oval(2, 2, 12, 12, fill=self.MUTED, outline="")

        tk.Frame(root, bg=self.BORDER, height=1).pack(fill="x", padx=PAD)

        # ショートカット編集ボタン
        tk.Button(
            root, text="ショートカット編集",
            bg=self.SURFACE, fg=self.TEXT,
            font=("Courier New", 10),
            relief="flat", padx=14, pady=8,
            cursor="hand2",
            activebackground=self.BORDER,
            command=self._open_shortcut_editor,
        ).pack(pady=12)

        # フッター
        tk.Label(
            root, text="ウィンドウを閉じるとサーバーが停止する",
            bg=self.BG, fg=self.BORDER,
            font=("Courier New", 7), pady=6,
        ).pack()

        # 中央配置
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

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _open_shortcut_editor(self):
        ShortcutEditorWindow(self.root)

    def _start_status_update(self):
        def update():
            n = len(connected_clients)
            self.client_count_var.set(str(n))
            self.lamp.itemconfig(self.lamp_circle,
                                 fill=self.ACCENT2 if n > 0 else self.MUTED)
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
