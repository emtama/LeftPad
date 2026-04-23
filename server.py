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
  WebSocket接続直後の認証メッセージで検証する。
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
GESTURES_FILE  = os.path.join(BASE_DIR, "gestures.json")
ACCESS_TOKEN   = secrets.token_urlsafe(16)   # 起動ごとに再生成

DEFAULT_GESTURES = {
    "swipe_up": "拡大",
    "swipe_down": "縮小",
    "swipe_left": "元に戻す",
    "swipe_right": "やり直し",
    "long_press": "スポイト",
    "hold_screen": "スポイト",
    "pinch_in": "縮小",
    "pinch_out": "拡大",
    "double_tap": "全画面",
    "two_finger_tap": "元に戻す",
    "two_finger_swipe_up": "拡大",
    "two_finger_swipe_down": "縮小",
    "two_finger_swipe_left": "元に戻す",
    "two_finger_swipe_right": "やり直し",
    "three_finger_tap": "全画面",
    "three_finger_swipe_up": "レイヤー上",
    "three_finger_swipe_down": "レイヤー下",
    "three_finger_swipe_left": "左回転",
    "three_finger_swipe_right": "右回転",
}

COMMAND_ALIASES = {
    "undo": "元に戻す",
    "redo": "やり直し",
    "save": "保存",
    "deselect": "選択解除",
    "brush": "ブラシ",
    "eraser": "消しゴム",
    "fill": "塗りつぶし",
    "eyedrop": "スポイト",
    "select_rect": "矩形選択",
    "move": "移動",
    "pen": "ペン",
    "text": "テキスト",
    "lasso": "なげなわ",
    "swap_color": "前後景切替",
    "reset_color": "白黒リセット",
    "brush_size_up": "ブラシサイズアップ",
    "brush_size_down": "ブラシサイズダウン",
    "zoom_in": "拡大",
    "zoom_out": "縮小",
    "zoom_fit": "全体表示",
    "zoom_100": "100%表示",
    "rotate_cw": "右回転",
    "rotate_ccw": "左回転",
    "rotate_reset": "回転リセット",
    "flip_h": "左右反転",
    "fullscreen": "全画面",
    "layer_new": "レイヤー新規",
    "layer_delete": "レイヤー削除",
    "layer_merge": "レイヤー結合",
    "layer_duplicate": "レイヤー複製",
    "layer_up": "レイヤー上",
    "layer_down": "レイヤー下",
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

def load_gestures() -> dict:
    if not os.path.exists(GESTURES_FILE):
        save_gestures(DEFAULT_GESTURES.copy())
        return DEFAULT_GESTURES.copy()
    try:
        with open(GESTURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("gestures.json must be object")
        merged = DEFAULT_GESTURES.copy()
        merged.update({k: str(v) for k, v in data.items()})
        return merged
    except Exception as e:
        log.error(f"gestures.json の読み込みに失敗: {e}")
        return DEFAULT_GESTURES.copy()

def save_gestures(data: dict) -> bool:
    try:
        with open(GESTURES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        log.info("gestures.json を保存した")
        return True
    except Exception as e:
        log.error(f"gestures.json の保存に失敗: {e}")
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

def parse_raw_cmd(cmd_str: str) -> list[str]:
    return [k.strip().lower() for k in cmd_str.split("+")]

def resolve_command_name(cmd: str) -> str:
    if cmd in COMMAND_ALIASES:
        return COMMAND_ALIASES[cmd]
    return cmd

# ══════════════════════════════════════════════
#  WebSocket ハンドラ
# ══════════════════════════════════════════════
async def ws_handler(websocket):
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

    if data.get("type") != "auth":
        log.warning(f"認証失敗(type不正): {client[0]}")
        await websocket.close(4001, "Unauthorized")
        return

    token = data.get("token", "")
    if token != ACCESS_TOKEN:
        log.warning(f"不正アクセス拒否: {client[0]} (token不一致)")
        await websocket.send(json.dumps({"type": "auth", "ok": False}))
        await websocket.close(4001, "Unauthorized")
        return

    await websocket.send(json.dumps({"type": "auth", "ok": True}))

    log.info(f"WS 接続: {client[0]}:{client[1]}")
    connected_clients.add(websocket)
    shortcuts = load_shortcuts()
    gestures = load_gestures()

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

            if msg_type == "get_gestures":
                await websocket.send(json.dumps({"type": "gestures", "data": gestures}))
                continue

            # ── ショートカットを更新・保存 ────────────
            if msg_type == "update_shortcut":
                cmd  = resolve_command_name(data.get("cmd", "").strip())
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

            if msg_type == "update_gesture":
                gkey = data.get("gesture", "").strip()
                cmd = data.get("cmd", "").strip()
                if not gkey:
                    await websocket.send(json.dumps({"type": "gesture_updated", "ok": False}))
                    continue
                gestures[gkey] = cmd
                ok = save_gestures(gestures)
                await websocket.send(json.dumps({
                    "type": "gesture_updated", "ok": ok, "gesture": gkey, "cmd": cmd
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

            cmd = resolve_command_name(cmd)
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
    MODIFIERS = {"ctrl", "shift", "alt", "meta"}
    GESTURE_KEYS = [
        "swipe_up", "swipe_down", "swipe_left", "swipe_right",
        "long_press", "hold_screen", "pinch_in", "pinch_out", "double_tap",
        "two_finger_tap", "two_finger_swipe_up", "two_finger_swipe_down",
        "two_finger_swipe_left", "two_finger_swipe_right",
        "three_finger_tap", "three_finger_swipe_up", "three_finger_swipe_down",
        "three_finger_swipe_left", "three_finger_swipe_right",
    ]
    GESTURE_LABELS = {
        "swipe_up": "上スワイプ",
        "swipe_down": "下スワイプ",
        "swipe_left": "左スワイプ",
        "swipe_right": "右スワイプ",
        "long_press": "長押し",
        "hold_screen": "画面ホールド",
        "pinch_in": "ピンチイン",
        "pinch_out": "ピンチアウト",
        "double_tap": "ダブルタップ",
        "two_finger_tap": "二本指タップ",
        "two_finger_swipe_up": "二本指上スワイプ",
        "two_finger_swipe_down": "二本指下スワイプ",
        "two_finger_swipe_left": "二本指左スワイプ",
        "two_finger_swipe_right": "二本指右スワイプ",
        "three_finger_tap": "三本指タップ",
        "three_finger_swipe_up": "三本指上スワイプ",
        "three_finger_swipe_down": "三本指下スワイプ",
        "three_finger_swipe_left": "三本指左スワイプ",
        "three_finger_swipe_right": "三本指右スワイプ",
    }

    def __init__(self, parent_root):
        super().__init__(parent_root)
        self.title("LeftPad ─ キー/ジェスチャー編集")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(860, 560)

        self._shortcuts = load_shortcuts()
        self._gestures  = load_gestures()
        self._entries   = {}   # cmd → StringVar("+区切り")
        self._shortcut_rows = {}  # cmd -> Frame
        self._deleted_shortcuts: set[str] = set()
        self._gesture_entries = {}  # gesture_key -> StringVar (キー組み合わせ)
        self._capture_target = None
        self._capture_pressed: set[str] = set()
        self._capture_candidate: list[str] = []
        self._capture_buttons = {}
        self._active_capture_button = None
        self._new_shortcut_keys_var = tk.StringVar(value="")
        self._new_shortcut_cmd_var = tk.StringVar(value="")
        self._body_canvas = None
        self._body_scrollbar = None
        self._body_inner = None

        self._build()
        self._maximize()
        self.focus_force()
        self.bind_all("<KeyPress>", self._on_key_press, add="+")
        self.bind_all("<KeyRelease>", self._on_key_release, add="+")

    # ── UI ───────────────────────────────────
    def _build(self):
        tk.Label(
            self, text="キー / ジェスチャー編集",
            bg=self.BG, fg=self.ACCENT,
            font=("Courier New", 14, "bold"), pady=12,
        ).pack()

        tk.Label(
            self,
            text="割り当て変更: [キーを記録] → キーボードを押す（入力ではなく実キー記録）",
            bg=self.BG, fg=self.MUTED, font=("Courier New", 9),
        ).pack(pady=(0, 8))

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", padx=16)

        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=16, pady=8)
        self._build_scrollable_body(body)

        wrapper = tk.Frame(self._body_inner, bg=self.BG)
        wrapper.pack(fill="both", expand=True)

        left = tk.Frame(wrapper, bg=self.BG)
        right = tk.Frame(wrapper, bg=self.BG)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))

        self._build_shortcuts_panel(left)
        self._build_gestures_panel(right)
        self._build_add_shortcut_panel(self._body_inner)

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", padx=16)
        footer = tk.Frame(self, bg=self.BG, pady=12)
        footer.pack(fill="x")
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

    def _build_scrollable_body(self, parent):
        self._body_canvas = tk.Canvas(parent, bg=self.BG, highlightthickness=0, bd=0)
        self._body_scrollbar = tk.Scrollbar(parent, orient="vertical", command=self._body_canvas.yview)
        self._body_canvas.configure(yscrollcommand=self._body_scrollbar.set)
        self._body_canvas.pack(side="left", fill="both", expand=True)
        self._body_scrollbar.pack(side="right", fill="y")

        self._body_inner = tk.Frame(self._body_canvas, bg=self.BG)
        window_id = self._body_canvas.create_window((0, 0), window=self._body_inner, anchor="nw")
        self._body_inner.bind(
            "<Configure>",
            lambda e: self._body_canvas.configure(scrollregion=self._body_canvas.bbox("all"))
        )
        self._body_canvas.bind(
            "<Configure>",
            lambda e: self._body_canvas.itemconfigure(window_id, width=e.width)
        )
        self._body_canvas.bind_all("<MouseWheel>", self._on_mouse_wheel, add="+")
        self._body_canvas.bind_all("<Button-4>", self._on_mouse_wheel, add="+")
        self._body_canvas.bind_all("<Button-5>", self._on_mouse_wheel, add="+")

    def _on_mouse_wheel(self, event):
        if not self.winfo_exists():
            return
        if getattr(event, "num", None) == 4:
            self._body_canvas.yview_scroll(-1, "units")
        elif getattr(event, "num", None) == 5:
            self._body_canvas.yview_scroll(1, "units")
        elif getattr(event, "delta", 0) != 0:
            self._body_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _build_shortcuts_panel(self, parent):
        tk.Label(parent, text="ショートカットキー", bg=self.BG, fg=self.ACCENT2,
                 font=("Courier New", 11, "bold")).pack(anchor="w")
        box = tk.Frame(parent, bg=self.SURFACE, padx=8, pady=8)
        box.pack(fill="both", expand=True, pady=(6, 0))
        hdr = tk.Frame(box, bg=self.SURFACE)
        hdr.pack(fill="x", pady=(0, 4))
        tk.Label(hdr, text="コマンド", width=22, bg=self.SURFACE,
                 fg=self.MUTED, font=("Courier New", 9, "bold"), anchor="w").grid(row=0, column=0, padx=(4, 8))
        tk.Label(hdr, text="割り当てキー", width=22, bg=self.SURFACE,
                 fg=self.MUTED, font=("Courier New", 9, "bold"), anchor="w").grid(row=0, column=1)
        tk.Label(hdr, text="操作", width=12, bg=self.SURFACE,
                 fg=self.MUTED, font=("Courier New", 9, "bold"), anchor="w").grid(row=0, column=2, columnspan=2)

        real = get_real_shortcuts(self._shortcuts)
        for i, (cmd, keys) in enumerate(real.items()):
            row_bg = self.SURFACE if i % 2 == 0 else self.BG
            row = tk.Frame(box, bg=row_bg, pady=3)
            row.pack(fill="x")
            tk.Label(
                row, text=cmd, width=22,
                bg=row_bg, fg=self.TEXT,
                font=("Courier New", 9), anchor="w",
            ).grid(row=0, column=0, padx=(4, 8))
            var = tk.StringVar(value="+".join(keys))
            tk.Label(
                row, textvariable=var, width=22,
                bg=self.SURFACE, fg=self.ACCENT2,
                font=("Courier New", 10), anchor="w",
            ).grid(row=0, column=1, padx=4)
            btn = tk.Button(
                row, text="キーを記録",
                bg=self.BORDER, fg=self.TEXT, relief="flat",
                font=("Courier New", 8), cursor="hand2",
                command=lambda c=cmd: self._start_capture(("shortcut", c)),
            )
            btn.grid(row=0, column=2, padx=4)
            del_btn = tk.Button(
                row, text="削除",
                bg=self.DANGER, fg=self.BG, relief="flat",
                font=("Courier New", 8), cursor="hand2",
                command=lambda c=cmd: self._delete_shortcut(c),
            )
            del_btn.grid(row=0, column=3, padx=4)
            self._capture_buttons[("shortcut", cmd)] = btn
            self._shortcut_rows[cmd] = row
            self._entries[cmd] = var

    def _build_gestures_panel(self, parent):
        tk.Label(parent, text="ジェスチャー割り当て", bg=self.BG, fg=self.ACCENT2,
                 font=("Courier New", 11, "bold")).pack(anchor="w")
        box = tk.Frame(parent, bg=self.SURFACE, padx=8, pady=8)
        box.pack(fill="both", expand=True, pady=(6, 0))
        for key in self.GESTURE_KEYS:
            row = tk.Frame(box, bg=self.SURFACE, pady=2)
            row.pack(fill="x")
            label = self.GESTURE_LABELS.get(key, key)
            tk.Label(row, text=label, width=24, anchor="w", bg=self.SURFACE, fg=self.TEXT,
                     font=("Courier New", 9)).pack(side="left")
            cmd = self._gestures.get(key, "")
            combo = "+".join(self._shortcuts.get(cmd, [])) if cmd in self._shortcuts else ""
            var = tk.StringVar(value=combo)
            ent = tk.Entry(row, textvariable=var, bg=self.BG, fg=self.ACCENT2,
                           font=("Courier New", 9), relief="flat")
            ent.pack(side="left", fill="x", expand=True, padx=(4, 0))
            rec_btn = tk.Button(
                row, text="キーを記録",
                bg=self.BORDER, fg=self.TEXT, relief="flat",
                font=("Courier New", 8), cursor="hand2",
                command=lambda g=key: self._start_capture(("gesture", g)),
            )
            rec_btn.pack(side="left", padx=(6, 4))
            del_btn = tk.Button(
                row, text="削除",
                bg=self.DANGER, fg=self.BG, relief="flat",
                font=("Courier New", 8), cursor="hand2",
                command=lambda g=key: self._delete_gesture(g),
            )
            del_btn.pack(side="left", padx=(0, 2))
            self._capture_buttons[("gesture", key)] = rec_btn
            self._gesture_entries[key] = var

    def _build_add_shortcut_panel(self, parent):
        panel = tk.Frame(parent, bg=self.SURFACE, padx=10, pady=8)
        panel.pack(fill="x", pady=(8, 0))
        tk.Label(panel, text="ショートカット追加", bg=self.SURFACE, fg=self.ACCENT2,
                 font=("Courier New", 10, "bold")).pack(side="left", padx=(0, 10))
        tk.Entry(panel, textvariable=self._new_shortcut_cmd_var, width=24, bg=self.BG, fg=self.TEXT,
                 font=("Courier New", 10), relief="flat").pack(side="left")
        tk.Label(panel, textvariable=self._new_shortcut_keys_var, width=22, anchor="w",
                 bg=self.BG, fg=self.ACCENT2, font=("Courier New", 10)).pack(side="left", padx=6)
        btn = tk.Button(panel, text="キーを記録", bg=self.BORDER, fg=self.TEXT, relief="flat",
                        command=lambda: self._start_capture(("new_shortcut", "")))
        btn.pack(side="left", padx=4)
        self._capture_buttons[("new_shortcut", "")] = btn
        tk.Button(panel, text="追加", bg=self.ACCENT2, fg=self.BG, relief="flat",
                  command=self._add_shortcut).pack(side="left", padx=4)

    def _start_capture(self, target):
        if self._capture_target and self._capture_target != target:
            return
        if self._capture_target == target:
            self._confirm_capture()
            return
        self._capture_target = target
        self._capture_pressed.clear()
        self._capture_candidate = []
        self._active_capture_button = self._capture_buttons.get(target)
        for key, button in self._capture_buttons.items():
            if key == target:
                button.configure(text="確定", bg=self.ACCENT2, fg=self.BG, state="normal")
            else:
                button.configure(state="disabled", bg=self.MUTED, fg=self.BG)

    @staticmethod
    def _normalize_key(keysym: str) -> str:
        k = keysym.lower()
        mapping = {
            "control_l": "ctrl", "control_r": "ctrl",
            "shift_l": "shift", "shift_r": "shift",
            "alt_l": "alt", "alt_r": "alt",
            "meta_l": "meta", "meta_r": "meta",
            "return": "enter", "escape": "esc", "prior": "pageup", "next": "pagedown",
            "backspace": "backspace", "space": "space",
        }
        return mapping.get(k, k)

    def _on_key_press(self, event):
        if not self._capture_target:
            return
        self._capture_pressed.add(self._normalize_key(event.keysym))
        self._update_capture_candidate()

    def _on_key_release(self, event):
        if not self._capture_target:
            return
        key = self._normalize_key(event.keysym)
        if key in self._capture_pressed:
            self._capture_pressed.remove(key)
        if not self._capture_candidate and key:
            self._update_capture_candidate(fallback_key=key)

    def _update_capture_candidate(self, fallback_key=None):
        keys = set(self._capture_pressed)
        if not keys and fallback_key:
            keys.add(fallback_key)
        if not keys:
            return
        modifiers = [k for k in ("ctrl", "shift", "alt", "meta") if k in keys]
        non_mods = sorted([k for k in keys if k not in self.MODIFIERS])
        combo = modifiers + (non_mods if non_mods else [fallback_key] if fallback_key in self.MODIFIERS else [])
        combo = [k for i, k in enumerate(combo) if k and k not in combo[:i]]
        if not combo:
            return
        self._capture_candidate = combo
        joined = "+".join(combo)
        ttype, tkey = self._capture_target
        if ttype == "shortcut" and tkey in self._entries:
            self._entries[tkey].set(joined)
        elif ttype == "gesture" and tkey in self._gesture_entries:
            self._gesture_entries[tkey].set(joined)
        elif ttype == "new_shortcut":
            self._new_shortcut_keys_var.set(joined)

    def _confirm_capture(self):
        if not self._capture_target:
            return
        if not self._capture_candidate:
            messagebox.showwarning("LeftPad", "キーを押してから確定してください", parent=self)
            return
        self._capture_target = None
        self._capture_pressed.clear()
        self._capture_candidate = []
        self._active_capture_button = None
        for button in self._capture_buttons.values():
            button.configure(text="キーを記録", state="normal", bg=self.BORDER, fg=self.TEXT)

    def _add_shortcut(self):
        cmd = self._new_shortcut_cmd_var.get().strip()
        keys = self._new_shortcut_keys_var.get().strip()
        if not cmd or not keys:
            messagebox.showwarning("LeftPad", "コマンド名とキーを設定する", parent=self)
            return
        if cmd in self._entries:
            messagebox.showwarning("LeftPad", "既に存在するコマンド", parent=self)
            return
        self._entries[cmd] = tk.StringVar(value=keys)
        self._deleted_shortcuts.discard(cmd)
        self._shortcuts[cmd] = [k.strip() for k in keys.split("+") if k.strip()]
        self._new_shortcut_cmd_var.set("")
        self._new_shortcut_keys_var.set("")
        messagebox.showinfo("LeftPad", "追加した。保存を押して確定する", parent=self)

    def _delete_shortcut(self, cmd):
        if cmd not in self._entries:
            return
        if not messagebox.askyesno("LeftPad", f"「{cmd}」を削除しますか？", parent=self):
            return
        if self._capture_target == ("shortcut", cmd):
            self._capture_target = None
            self._capture_pressed.clear()
            self._capture_candidate = []
            for button in self._capture_buttons.values():
                button.configure(text="キーを記録", state="normal", bg=self.BORDER, fg=self.TEXT)
        self._capture_buttons.pop(("shortcut", cmd), None)
        row = self._shortcut_rows.pop(cmd, None)
        if row is not None:
            row.destroy()
        self._entries.pop(cmd, None)
        self._deleted_shortcuts.add(cmd)

    def _delete_gesture(self, gesture_key):
        if gesture_key not in self._gesture_entries:
            return
        if not messagebox.askyesno(
            "LeftPad",
            f"ジェスチャー「{self.GESTURE_LABELS.get(gesture_key, gesture_key)}」の割り当てを削除しますか？",
            parent=self
        ):
            return
        self._gesture_entries[gesture_key].set("")

    def _save(self):
        changed = 0
        deleted = 0
        for cmd in list(self._deleted_shortcuts):
            if cmd in self._shortcuts:
                del self._shortcuts[cmd]
                deleted += 1
        self._deleted_shortcuts.clear()
        for cmd, var in self._entries.items():
            raw = var.get().strip()
            if not raw:
                continue
            new_keys = [k.strip().lower() for k in raw.split("+") if k.strip()]
            if new_keys and self._shortcuts.get(cmd) != new_keys:
                self._shortcuts[cmd] = new_keys
                changed += 1

        combo_to_cmd = {}
        for cmd, keys in self._shortcuts.items():
            if not isinstance(keys, list):
                continue
            combo = "+".join([k.strip().lower() for k in keys if k.strip()])
            if combo and combo not in combo_to_cmd:
                combo_to_cmd[combo] = cmd

        gesture_changed = 0
        for gkey, var in self._gesture_entries.items():
            combo = var.get().strip().lower()
            if not combo:
                cmd = ""
            else:
                cmd = combo_to_cmd.get(combo)
                if not cmd:
                    messagebox.showwarning(
                        "LeftPad",
                        f"ジェスチャー「{self.GESTURE_LABELS.get(gkey, gkey)}」のキー[{combo}]に対応するショートカットがない",
                        parent=self
                    )
                    return
            if self._gestures.get(gkey, "") != cmd:
                self._gestures[gkey] = cmd
                gesture_changed += 1

        if changed == 0 and deleted == 0 and gesture_changed == 0:
            messagebox.showinfo("LeftPad", "変更なし", parent=self)
            return

        ok_shortcuts = save_shortcuts(self._shortcuts)
        ok_gestures = save_gestures(self._gestures)
        if ok_shortcuts and ok_gestures:
            messagebox.showinfo(
                "LeftPad",
                f"ショートカット変更 {changed} 件 / 削除 {deleted} 件 / ジェスチャー {gesture_changed} 件を保存",
                parent=self
            )
            self.destroy()
        else:
            messagebox.showerror("LeftPad", "保存に失敗した", parent=self)

    def _maximize(self):
        self.update_idletasks()
        try:
            self.state("zoomed")
        except tk.TclError:
            try:
                self.attributes("-zoomed", True)
            except tk.TclError:
                self.geometry(f"{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0")

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
