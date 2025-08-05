import os
import sys
import asyncio
import threading
import ctypes
import time
import random
import tkinter as tk
from tkinter import filedialog
import json

from pynput import mouse, keyboard
from PyQt5 import QtWidgets, QtGui, QtCore
from screeninfo import get_monitors
import websockets
import webview
import base64
import tempfile

import winreg

# ---------------- Config ----------------
the_url = "http://localhost:5500/dev.html"  # change if you host files locally

# ---------------- File Save Function and COLOR setter ----------------
def save_text_file(data):
    root = tk.Tk()
    root.withdraw()
    file_path = filedialog.asksaveasfilename(
        defaultextension=".txt",
        initialfile="settings.txt",
        filetypes=[("Text files", "*.txt")]
    )
    if file_path:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(data)
        print(f"Message saved to {file_path}")
    else:
        print("Save cancelled")

def hex_to_bgr(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 8:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    elif len(hex_color) == 6:
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
    else:
        raise ValueError("Hex color must be 6 or 8 characters")
    return b, g, r

def set_windows_accent_color_hex(hex_color):
    b, g, r = hex_to_bgr(hex_color)
    color_dword = (b << 16) | (g << 8) | r

    key_path = r"Software\Microsoft\Windows\DWM"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "AccentColor", 0, winreg.REG_DWORD, color_dword)

    key_path2 = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path2, 0, winreg.KEY_SET_VALUE) as key2:
            winreg.SetValueEx(key2, "AccentColorMenu", 0, winreg.REG_DWORD, color_dword)
    except FileNotFoundError:
        pass

    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x1A, 0, "ImmersiveColorSet", 0x2, 500)

# ---------------- PyQt Overlay ----------------
class TransparentOverlay(QtWidgets.QWidget):
    def __init__(self, image_path, opacity, grayscale, size_percent):
        super().__init__()
        self.anim = None

        temp_file_path = None

        if len(image_path) > 100 and not os.path.exists(image_path):
            try:
                clean_b64 = ''.join(image_path.strip().splitlines()).replace('\r', '').replace('\n', '')
                padded_b64 = clean_b64 + '=' * ((4 - len(clean_b64) % 4) % 4)
                img_bytes = base64.b64decode(padded_b64)
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
                temp_file.write(img_bytes)
                temp_file.close()
                temp_file_path = temp_file.name
                image_path = temp_file_path
            except Exception as e:
                print("[ERROR] Failed to decode base64 image:", e)

        original_image = QtGui.QPixmap(image_path)
        if original_image.isNull():
            raise ValueError("Failed to load image.")

        if grayscale:
            image = original_image.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
            original_image = QtGui.QPixmap.fromImage(image)

        self.opacity = max(0.0, min(opacity, 1.0))

        monitor = next((m for m in get_monitors() if m.is_primary), get_monitors()[0])
        screen_x = monitor.x
        screen_y = monitor.y
        screen_w = monitor.width
        screen_h = monitor.height

        if grayscale:
            new_width = int(original_image.width() * (size_percent / 100))
            new_height = int(original_image.height() * (size_percent / 100))
            self.image = original_image.scaled(new_width, new_height, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            x = screen_w // 2 - self.image.width() // 2
            y = screen_h // 2 - self.image.height() // 2
            self.setGeometry(screen_x + x, screen_y + y, self.image.width(), self.image.height())
            self.setFixedSize(self.image.width(), self.image.height())
        else:
            self.image = original_image
            self.setGeometry(screen_x, screen_y, self.image.width(), self.image.height())
            self.setFixedSize(self.image.width(), self.image.height())

        self.setAttribute(QtCore.Qt.WA_TranslucentBackground)
        self.setWindowFlags(
            QtCore.Qt.FramelessWindowHint |
            QtCore.Qt.WindowStaysOnTopHint |
            QtCore.Qt.Tool |
            QtCore.Qt.WindowTransparentForInput
        )

        self.setWindowOpacity(0.0)
        self.show()
        self.fade_in()

        if temp_file_path:
            QtCore.QTimer.singleShot(5000, lambda: os.remove(temp_file_path))

    def fade_in(self):
        self.anim = QtCore.QPropertyAnimation(self, b"windowOpacity")
        self.anim.setDuration(500)
        self.anim.setStartValue(0.0)
        self.anim.setEndValue(self.opacity)
        self.anim.start()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setOpacity(1.0)
        painter.drawPixmap(0, 0, self.image)

# ---------------- Recoil patterns ----------------
recoil_patterns = {
    "Operator1": [(0, 2), (1, 3), (0, 1), (-1, 2)],
    "Operator2": [(0, 1), (2, 2), (1, 1), (-1, 0)]
}

# ---------------- Main Logic ----------------
def run():
    fire_delay = 0.01
    recoil_x = 0
    recoil_y = 0

    opacity = 1.0
    grayscale = False
    size_percent = 100
    reticle_path = "pure_default_reticle.png"

    primary_key = '1'
    secondary_key = '2'
    pause_key = 'p'

    holding = False
    left_held = False
    right_held = False

    paused = False
    pause_lock = threading.Lock()
    ws_client = None
    ws_lock = threading.Lock()
    loop = asyncio.new_event_loop()

    qt_app = None
    overlay_widget = None
    overlay_thread = None

    current_pattern = recoil_patterns.get("Operator1", [])
    recoil_index = 0
    sensitivity = 1.0

    def set_hotkeys(data):
        nonlocal primary_key, secondary_key, pause_key
        try:
            pause_key = str(data[4])
            primary_key = data[5]
            secondary_key = data[6]
        except IndexError:
            print("[WARN] Invalid hotkey data received:", data)

    def move_mouse(dx, dy):
        ctypes.windll.user32.mouse_event(0x0001, int(dx), int(dy), 0, 0)

    def recoil_loop():
        nonlocal recoil_index
        while True:
            with pause_lock:
                if holding and not paused and current_pattern:
                    dx, dy = current_pattern[recoil_index]
                    move_mouse(dx * sensitivity, dy * sensitivity)
                    recoil_index = (recoil_index + 1) % len(current_pattern)
            time.sleep(fire_delay)

    def on_click(x, y, button, pressed):
        nonlocal left_held, right_held, holding
        if button == mouse.Button.left:
            left_held = pressed
        elif button == mouse.Button.right:
            right_held = pressed
            time.sleep(0.02)
            send_ws_message('RIGHT_CLICK')
        holding = left_held and right_held

    def send_ws_message(message):
        asyncio.run_coroutine_threadsafe(_send_ws(message), loop)

    async def _send_ws(message):
        nonlocal ws_client
        try:
            if ws_client:
                await ws_client.send(message)
        except Exception as e:
            print(f"WebSocket send error: {e}")

    def on_release(key):
        nonlocal paused
        try:
            if hasattr(key, 'char'):
                if key.char == primary_key:
                    send_ws_message("PRIMARY")
                elif key.char == secondary_key:
                    send_ws_message("SECONDARY")
                elif key.char == pause_key:
                    with pause_lock:
                        paused = not paused
                        send_ws_message("Paused" if paused else "Resumed")
        except AttributeError:
            pass

    def show_crosshair(new_opacity, new_grayscale, new_size, new_image):
        nonlocal qt_app, overlay_widget, overlay_thread

        def start_overlay():
            nonlocal qt_app, overlay_widget
            qt_app = QtWidgets.QApplication.instance()
            if qt_app is None:
                qt_app = QtWidgets.QApplication([])
            overlay_widget = TransparentOverlay(new_image, new_opacity, new_grayscale, new_size)
            qt_app.exec_()

        if overlay_thread and overlay_thread.is_alive():
            if overlay_widget:
                overlay_widget.close()
                overlay_widget = None
            if QtWidgets.QApplication.instance():
                QtWidgets.QApplication.quit()
            overlay_thread.join()
            qt_app = None

        overlay_thread = threading.Thread(target=start_overlay, daemon=True)
        overlay_thread.start()

    async def handler(websocket):
        nonlocal fire_delay, recoil_x, recoil_y, paused, ws_client
        nonlocal opacity, grayscale, size_percent, reticle_path
        nonlocal overlay_widget
        nonlocal current_pattern, recoil_index, sensitivity

        with ws_lock:
            ws_client = websocket
        try:
            async for message in websocket:
                # Try to parse JSON message (license verification requests will be JSON)
                try:
                    data = json.loads(message)
                    if data.get("action") == "verify":
                        key = data.get("key", "")
                        # For testing, we mock success:
                        success, msg = True, "License verified successfully"
                        await websocket.send(json.dumps({
                            "action": "license_result",
                            "ok": success,
                            "msg": msg
                        }))
                        continue
                except json.JSONDecodeError:
                    pass

                if message.startswith(">"):
                    try:
                        parts = message[1:].split(',')
                        opacity = float(parts[0])
                        grayscale = bool(int(float(parts[1])))
                        size_percent = float(parts[2])
                        reticle_path = parts[3]
                        show_crosshair(opacity, grayscale, size_percent, reticle_path)
                    except Exception as e:
                        print(f"Overlay update failed: {e}")

                elif message.startswith("<"):
                    if overlay_widget:
                        overlay_widget.close()
                        overlay_widget = None
                    await websocket.send("Overlay deactivated")

                elif message.startswith("^^^"):
                    save_text_file(message)

                elif message.startswith("HKEY"):
                    set_hotkeys(message)

                elif message.startswith("#"):
                    set_windows_accent_color_hex(message)

                elif message.startswith("operator:"):
                    op = message.split(":", 1)[1]
                    current_pattern = recoil_patterns.get(op, [])
                    recoil_index = 0
                    await websocket.send(f"Operator changed to {op}")

                elif message.startswith("sensitivity:"):
                    try:
                        sensitivity = float(message.split(":", 1)[1])
                        await websocket.send(f"Sensitivity set to {sensitivity}")
                    except ValueError:
                        await websocket.send("Error: Invalid sensitivity value")

                else:
                    try:
                        fire_delay, recoil_x, recoil_y = map(float, message.split(','))
                        with pause_lock:
                            paused = False
                        await websocket.send(f"Settings updated to: {message}")
                    except ValueError:
                        await websocket.send("Error: Invalid format.")

        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            with ws_lock:
                ws_client = None

    async def websocket_server():
        async with websockets.serve(handler, "localhost", 8765):
            await asyncio.Future()  # run forever

    def start_ws():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(websocket_server())

    def start_threads():
        threading.Thread(target=recoil_loop, daemon=True).start()
        threading.Thread(target=start_ws, daemon=True).start()
        mouse.Listener(on_click=on_click).start()
        threading.Thread(target=lambda: keyboard.Listener(on_release=on_release).run(), daemon=True).start()

    return start_threads

# ---------------- Start Everything ----------------
if __name__ == "__main__":
    start_threads = run()
    start_threads()  # start background threads and listeners

    webview.create_window(
        'Ｐｕｒｅ　Ｓｃｒｉｐｔｓ',
        the_url,
        width=960,
        height=570
    )
    webview.start()
