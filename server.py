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
SHORTCUTS_FILE = os.path.join(BASE_DIR, "shortcuts.json")
GESTURES_FILE  = os.path.join(BASE_DIR, "gesture_shortcuts.json")
ACCESS_TOKEN   = secrets.token_urlsafe(16)   # 起動ごとに再生成

with open("gesture_labels.json", "r", encoding="utf-8") as f:
    GESTURE_LABELS_JP = json.load(f)
GESTURE_KEYS = list(GESTURE_LABELS_JP.keys())
DEFAULT_GESTURES = {
    key: [] for key in GESTURE_KEYS
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

def load_gestures() -> dict:
    """gesture_shortcuts.json を読み込む（ジェスチャー: キー配列の形式）"""
    if not os.path.exists(GESTURES_FILE):
        save_gestures(DEFAULT_GESTURES.copy())
        return DEFAULT_GESTURES.copy()
    try:
        with open(GESTURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("gesture_shortcuts.json must be object")
        # デフォルト値とマージ（キー配列形式を保持）
        merged = DEFAULT_GESTURES.copy()
        merged.update(data)
        return merged
    except Exception as e:
        log.error(f"gesture_shortcuts.json の読み込みに失敗: {e}")
        return DEFAULT_GESTURES.copy()

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

def parse_raw_cmd(cmd_str: str) -> list[str]:
    return [k.strip().lower() for k in cmd_str.split("+")]

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
    connected_client_infos[websocket] = f"{client[0]}:{client[1]}"
    gestures = load_gestures()

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"ok": False, "error": "invalid json"}))
                continue

            msg_type = data.get("type", "")   # "get_shortcuts" / "update_shortcut" / ""

            if msg_type == "get_gestures":
                await websocket.send(json.dumps({"type": "gestures", "data": gestures}))
                continue

            if msg_type == "get_settings":
                await websocket.send(json.dumps({"type": "settings", "data": APP_SETTINGS}))
                continue

            if msg_type == "haptics_status":
                supported = bool(data.get("supported", False))
                allowed = bool(data.get("allowed", False))
                if not supported or not allowed:
                    log.warning(f"端末ハプティクス警告: {client[0]} (supported={supported}, allowed={allowed})")
                else:
                    log.info(f"端末ハプティクス状態: {client[0]} 利用可能")
                continue

            if msg_type == "update_setting":
                key = data.get("key")
                value = data.get("value")
                if key in APP_SETTINGS and isinstance(value, bool):
                    APP_SETTINGS[key] = value
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": True, "key": key, "value": value}))
                else:
                    await websocket.send(json.dumps({"type": "setting_updated", "ok": False}))
                continue

            # ── ジェスチャーを更新・保存 ────────────
            if msg_type == "update_gesture":
                gkey = data.get("gesture", "").strip()
                keys = data.get("keys")
                if not gkey or not isinstance(keys, list) or not keys:
                    await websocket.send(json.dumps({"type": "gesture_updated", "ok": False}))
                    continue
                gestures[gkey] = keys
                ok = save_gestures(gestures)
                log.info(f"ジェスチャー更新: {gkey} → {'+'.join(keys)}")
                await websocket.send(json.dumps({
                    "type": "gesture_updated", "ok": ok, "gesture": gkey, "keys": keys
                }))
                continue

            # ── ジェスチャーコマンド実行 ────────────────
            gesture_name = data.get("gesture", "").strip()
            gesture_label = GESTURE_LABELS_JP.get(gesture_name, gesture_name)

            if not gesture_name or gesture_name not in gestures:
                await websocket.send(json.dumps({"ok": False, "error": "no gesture"}))
                continue

            keys = gestures[gesture_name]
            if not isinstance(keys, list) or not keys:
                await websocket.send(json.dumps({"ok": False, "error": "invalid gesture"}))
                continue

            try:
                execute_keys(keys)
                log.info(f"[ジェスチャー:{gesture_label}] {gesture_name:30s} → {'+'.join(keys)}")
                await websocket.send(json.dumps({"ok": True, "gesture": gesture_name, "keys": keys}))
            except Exception as e:
                log.error(f"キー実行エラー: {e}")
                await websocket.send(json.dumps({"ok": False, "error": str(e)}))

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

    def __init__(self, parent_root):
        super().__init__(parent_root)
        self.title("LeftPad ─ ジェスチャー編集")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(860, 560)

        self._gestures  = load_gestures()
        self._gesture_entries = {}  # gesture_key -> StringVar (キー配列)
        self._capture_target = None
        self._capture_pressed: set[str] = set()
        self._capture_candidate: list[str] = []
        self._capture_buttons = {}
        self._active_capture_button = None
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
            self, text="ジェスチャー編集",
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
        self._build_gestures_panel(wrapper)

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

    def _build_gestures_panel(self, parent):
        tk.Label(parent, text="ジェスチャー割り当て", bg=self.BG, fg=self.ACCENT2,
                 font=("Courier New", 11, "bold")).pack(anchor="w")
        box = tk.Frame(parent, bg=self.SURFACE, padx=8, pady=8)
        box.pack(fill="both", expand=True, pady=(6, 0))
        for key in GESTURE_KEYS:
            row = tk.Frame(box, bg=self.SURFACE, pady=2)
            row.pack(fill="x")
            label = self.GESTURE_LABELS.get(key, key)
            tk.Label(row, text=label, width=24, anchor="w", bg=self.SURFACE, fg=self.TEXT,
                     font=("Courier New", 9)).pack(side="left")
            keys = self._gestures.get(key, [])
            combo = "+".join(keys) if isinstance(keys, list) else str(keys)
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

    @staticmethod
    def _modifiers_from_state(state: int) -> set[str]:
        mods = set()
        if state & 0x0001:
            mods.add("shift")
        if state & 0x0004:
            mods.add("ctrl")
        if state & 0x0008:
            mods.add("alt")
        if state & 0x0080:
            mods.add("meta")
        return mods

    def _on_key_press(self, event):
        if not self._capture_target:
            return
        self._capture_pressed.add(self._normalize_key(event.keysym))
        self._capture_pressed.update(self._modifiers_from_state(event.state))
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
        if ttype == "gesture" and tkey in self._gesture_entries:
            self._gesture_entries[tkey].set(joined)

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
        gesture_changed = 0
        for gkey, var in self._gesture_entries.items():
            raw = var.get().strip()
            if not raw:
                keys = []
            else:
                keys = [k.strip().lower() for k in raw.split("+") if k.strip()]
            if self._gestures.get(gkey, []) != keys:
                self._gestures[gkey] = keys
                gesture_changed += 1

        if gesture_changed == 0:
            messagebox.showinfo("LeftPad", "変更なし", parent=self)
            return

        ok_gestures = save_gestures(self._gestures)
        if ok_gestures:
            messagebox.showinfo(
                "LeftPad",
                f"ジェスチャー {gesture_changed} 件を保存",
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


class InlineGestureEditor(tk.Frame):
    BG = ShortcutEditorWindow.BG
    SURFACE = ShortcutEditorWindow.SURFACE
    BORDER = ShortcutEditorWindow.BORDER
    ACCENT2 = ShortcutEditorWindow.ACCENT2
    TEXT = ShortcutEditorWindow.TEXT
    MUTED = ShortcutEditorWindow.MUTED
    DANGER = ShortcutEditorWindow.DANGER
    MODIFIERS = ShortcutEditorWindow.MODIFIERS

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

    def _build(self):
        tk.Label(self, text="ジェスチャー割り当て", bg=self.SURFACE, fg=self.ACCENT2, font=("Courier New", 12, "bold")).pack(anchor="w")
        body = tk.Frame(self, bg=self.SURFACE)
        body.pack(fill="both", expand=True, pady=(6, 0))
        canvas = tk.Canvas(body, bg=self.SURFACE, highlightthickness=0, bd=0)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        box = tk.Frame(canvas, bg=self.SURFACE)
        win = canvas.create_window((0, 0), window=box, anchor="nw")
        box.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        for key in GESTURE_KEYS:
            row = tk.Frame(box, bg=self.SURFACE, pady=2)
            row.pack(fill="x")
            tk.Label(row, text=GESTURE_LABELS_JP.get(key, key), width=16, anchor="w", bg=self.SURFACE, fg=self.TEXT, font=("Courier New", 10)).pack(side="left")
            cmd = self._gestures.get(key, "")
            combo = " + ".join(cmd) if isinstance(cmd, list) else str(cmd)
            var = tk.StringVar(value=combo)
            ent = tk.Entry(row, textvariable=var, width=18, bg=self.BG, fg=self.ACCENT2, relief="flat", font=("Courier New", 10))
            ent.pack(side="left", padx=(4, 6))
            btn = tk.Button(row, text="キーを記録", bg=self.BORDER, fg=self.TEXT, relief="flat", font=("Courier New", 9), command=lambda g=key: self._start_capture(g))
            btn.pack(side="left", padx=2)
            tk.Button(row, text="削除", bg=self.DANGER, fg=self.BG, relief="flat", font=("Courier New", 9), command=lambda g=key: self._delete(g)).pack(side="left", padx=2)
            var.trace_add("write", lambda *_args, g=key: self._save_one(g))
            self._gesture_vars[key] = var
            self._capture_buttons[key] = btn

    def _normalize(self, keysym):
        k = keysym.lower()
        return {"control_l":"ctrl","control_r":"ctrl","shift_l":"shift","shift_r":"shift","alt_l":"alt","alt_r":"alt","meta_l":"meta","meta_r":"meta","return":"enter","escape":"esc"}.get(k, k)

    def _start_capture(self, key):
        if self._capture_target and self._capture_target != key:
            return
        if self._capture_target == key:
            self._confirm_capture()
            return
        self._capture_target = key
        self._capture_pressed.clear()
        self._capture_candidate = []
        for k, b in self._capture_buttons.items():
            if k == key:
                b.configure(text="確定", bg=self.ACCENT2, fg=self.BG)
            else:
                b.configure(state="disabled", bg=self.MUTED, fg=self.BG)

    def _on_key_press(self, event):
        if not self._capture_target:
            return
        self._capture_pressed.add(self._normalize(event.keysym))
        mods = set()
        if event.state & 0x0001: mods.add("shift")
        if event.state & 0x0004: mods.add("ctrl")
        if event.state & 0x0008: mods.add("alt")
        self._capture_pressed.update(mods)
        self._update_candidate()

    def _on_key_release(self, _event):
        if self._capture_target:
            pass

    def _update_candidate(self):
        keys = set(self._capture_pressed)
        if not keys:
            return
        modifiers = [k for k in ("ctrl", "shift", "alt", "meta") if k in keys]
        non_mods = sorted([k for k in keys if k not in self.MODIFIERS])
        combo = modifiers + non_mods
        if not combo:
            return
        self._capture_candidate = combo
        self._gesture_vars[self._capture_target].set("+".join(combo))

    def _confirm_capture(self):
        # 既存値がある場合に候補未入力でもエラーにしない
        self._capture_target = None
        self._capture_pressed.clear()
        self._capture_candidate = []
        for b in self._capture_buttons.values():
            b.configure(text="キーを記録", state="normal", bg=self.BORDER, fg=self.TEXT)

    def _delete(self, key):
        self._gesture_vars[key].set("")

    def _save_one(self, gkey):
        combo = self._gesture_vars[gkey].get().strip().lower()
        combo_to_cmd = {}
        for cmd, keys in self._shortcuts.items():
            if isinstance(keys, list):
                c = "+".join([k.strip().lower() for k in keys if k.strip()])
                if c and c not in combo_to_cmd:
                    combo_to_cmd[c] = cmd
        self._gestures[gkey] = combo_to_cmd.get(combo, combo) if combo else ""
        save_gestures(self._gestures)

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
        self._maximize()

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
            font=("Courier New", 14), highlightthickness=0, bd=0
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

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    def _maximize(self):
        self.root.update_idletasks()
        try:
            self.root.state("zoomed")
        except tk.TclError:
            try:
                self.root.attributes("-zoomed", True)
            except tk.TclError:
                self.root.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")

    def _toggle_vibration(self):
        APP_SETTINGS["vibration_enabled"] = bool(self.vibration_var.get())
        log.info(f"スマホ振動設定: {'ON' if APP_SETTINGS['vibration_enabled'] else 'OFF'}")

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
