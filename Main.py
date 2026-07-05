import math
import os
import sys
import time
import threading
import serial
import serial.tools.list_ports
import spotipy
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'

import pygame
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv

# --- Resolve base directory whether frozen (PyInstaller) or running as .py ---
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(BASE_DIR, '.env'))

BAUD_RATE = 9600
SCOPE = "user-modify-playback-state user-read-playback-state"

VOLUME_STEP = 5
VOLUME_INTERVAL = 0.2

pygame.mixer.init()

ASSET_DIR = os.path.join(BASE_DIR, "SPARC_assets")
SOUND_CONNECTED = os.path.join(ASSET_DIR, "connected.mp3")
SOUND_DISCONNECTED = os.path.join(ASSET_DIR, "disconnected.mp3")


def find_hc05_port():
    """Auto-detect a paired HC-05 module's serial port."""
    for port in serial.tools.list_ports.comports():
        name = (port.device or "") + " " + (port.description or "")
        if "HC-05" in name:
            return port.device
    return None


def get_bt_port():
    """Priority: .env override -> auto-detect -> platform default."""
    env_port = os.getenv("BT_PORT")
    if env_port:
        return env_port

    detected = find_hc05_port()
    if detected:
        return detected

    if sys.platform == "darwin":
        return "/dev/cu.HC-05"
    elif sys.platform == "win32":
        return "COM7"
    elif sys.platform.startswith("linux"):
        return "/dev/rfcomm0"
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")


BT_PORT = get_bt_port()


def play_sound(path):
    try:
        pygame.mixer.music.load(path)
        pygame.mixer.music.play()
    except Exception as e:
        print(f"  Sound error: {e}")


def get_spotify():
    cache_dir = os.path.join(os.path.expanduser("~"), ".sparc_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, ".spotify_cache")

    auth = SpotifyOAuth(
        client_id=os.getenv("SPOTIPY_CLIENT_ID"),
        client_secret=os.getenv("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=os.getenv("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        open_browser=True,
        cache_path=cache_path,
    )
    return spotipy.Spotify(auth_manager=auth)


def send_current_volume(sp, ser):
    try:
        playback = sp.current_playback()
        if playback and playback.get("device"):
            vol = playback["device"].get("volume_percent", 0)
            ser.write(f"VOL{vol}\n".encode())
            ser.flush()
    except Exception:
        pass


# --- Volume ramping (background thread) ---

_volume_stop = threading.Event()
_volume_thread = None
_volume_lock = threading.Lock()


def _ramp_volume(sp, direction, ser):
    _volume_stop.clear()
    try:
        playback = sp.current_playback()
        if not playback or not playback.get("device"):
            return
        device = playback["device"]
        current = device.get("volume_percent", 50)
        device_id = device["id"]
    except Exception as e:
        print(f"  Volume error: {e}")
        return

    if (direction == 1 and current >= 100) or (direction == -1 and current <= 0):
        _volume_stop.set()
        ser.write(b"VS\n")
        ser.flush()
        print(f"  Already at {'max' if direction == 1 else 'min'} volume")
        return

    while not _volume_stop.wait(VOLUME_INTERVAL):
        try:
            current = max(0, min(100, current + direction * VOLUME_STEP))
            sp.volume(int(current), device_id=device_id)
            print(f"  Volume: {current}%")
            ser.write(f"VOL{int(current)}\n".encode())
            ser.flush()
            if current in (0, 100):
                _volume_stop.set()
                ser.write(b"VS\n")
                ser.flush()
                break
        except Exception as e:
            print(f"  Volume error: {e}")
            break


def start_volume(sp, direction, ser):
    global _volume_thread
    with _volume_lock:
        _volume_stop.set()
        if _volume_thread is not None and _volume_thread.is_alive():
            _volume_thread.join(timeout=0.5)
        _volume_thread = threading.Thread(target=_ramp_volume, args=(sp, direction, ser), daemon=True)
        _volume_thread.start()


def stop_volume():
    _volume_stop.set()


def volume_active():
    if _volume_thread is None:
        return False
    return _volume_thread.is_alive()


# --- Spotify actions ---

def next_track(sp, ser):
    sp.next_track()
    print("  Next track")
    time.sleep(0.3)
    send_current_volume(sp, ser)


def prev_track(sp, ser):
    try:
        playback = sp.current_playback()
        if not playback:
            return
        position = playback.get("progress_ms", 0)
        if position > 3000:
            sp.seek_track(0)
            print("  Restarted track")
        else:
            sp.previous_track()
            print("  Previous track")
    except spotipy.exceptions.SpotifyException as e:
        if "403" in str(e):
            print("  Previous track unavailable")
        else:
            raise
    time.sleep(0.3)
    send_current_volume(sp, ser)


def toggle_pause(sp, ser):
    playback = sp.current_playback()
    if playback and playback.get("is_playing"):
        sp.pause_playback()
        print("  Paused")
    else:
        devices = sp.devices().get("devices", [])
        device_id = devices[0]["id"] if devices else None
        sp.start_playback(device_id=device_id)
        print("  Resumed")
    time.sleep(0.3)
    send_current_volume(sp, ser)


def handle_stop(sp, ser):
    if volume_active():
        stop_volume()
        if _volume_thread is not None:
            _volume_thread.join(timeout=0.5)
        ser.write(b"VS\n")
        ser.flush()
        print("  Volume stopped")
    else:
        toggle_pause(sp, ser)


def get_handlers(ser):
    return {
        "S+": lambda sp: next_track(sp, ser),
        "S-": lambda sp: prev_track(sp, ser),
        "V+": lambda sp: start_volume(sp, +1, ser),
        "V-": lambda sp: start_volume(sp, -1, ser),
        "P":  lambda sp: handle_stop(sp, ser),
    }


def run_worker(stop_event, status):
    sp = get_spotify()

    print("Connecting to Spotify...")
    try:
        user = sp.current_user()
        user_name = user["display_name"]
        status["spotify"] = f"Logged in as {user_name}"
        status["spotify_state"] = "ok"
        print(f"Logged in as: {user_name}\n")
    except Exception as e:
        status["spotify"] = "Login failed"
        status["spotify_state"] = "err"
        print(f"Spotify auth failed: {e}")
        return

    ser = None
    arduino_connected = False
    spotify_connected = False
    was_connected = False
    HANDLERS = {}
    last_heartbeat = 0.0

    def update_spotify_status():
        nonlocal spotify_connected
        try:
            devices = sp.devices().get("devices", [])
            spotify_connected = bool(devices)
        except Exception:
            spotify_connected = False
        if spotify_connected:
            status["spotify"] = f"Logged in as {user_name}"
            status["spotify_state"] = "ok"
        else:
            status["spotify"] = "No active Spotify device"
            status["spotify_state"] = "err"

    while not stop_event.is_set():
        # --- Bluetooth reconnect ---
        if ser is None or not ser.is_open:
            try:
                status["arduino"] = f"Waiting for HC-05 on {BT_PORT}..."
                status["arduino_state"] = "wait"
                print(f"Waiting for HC-05 on {BT_PORT}...")
                ser = serial.Serial(BT_PORT, BAUD_RATE, timeout=1)
                time.sleep(1)
                ser.reset_input_buffer()
                HANDLERS = get_handlers(ser)
                arduino_connected = True
                status["arduino"] = "HC-05 connected"
                status["arduino_state"] = "ok"
                print("HC-05 connected.")
                update_spotify_status()
            except serial.SerialException:
                arduino_connected = False
                if stop_event.wait(2):
                    break
                update_spotify_status()
                continue

        # --- Evaluate combined state ---
        both_connected = arduino_connected and spotify_connected
        if both_connected and not was_connected:
            print("Connected.")
            play_sound(SOUND_CONNECTED)
            send_current_volume(sp, ser)
            was_connected = True
        elif not both_connected and was_connected:
            print("Disconnected.")
            play_sound(SOUND_DISCONNECTED)
            was_connected = False
            if ser and ser.is_open:
                try:
                    ser.write(b"VOL0\n")
                    ser.flush()
                except Exception:
                    pass

        if not arduino_connected:
            continue

        # --- Main loop ---
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                update_spotify_status()
                now = time.time()
                if now - last_heartbeat >= 2.0:
                    if ser and ser.is_open:
                        try:
                            ser.write(b"HB\n")
                            ser.flush()
                        except Exception:
                            pass
                    last_heartbeat = now
                continue

            print(f"← {line}")
            handler = HANDLERS.get(line)
            if handler:
                try:
                    handler(sp)
                except spotipy.exceptions.SpotifyException as e:
                    print(f"  Spotify error: {e}")
                except Exception as e:
                    print(f"  Error: {e}")
            else:
                print(f"  [unrecognized] {repr(line)}")

        except serial.SerialException:
            print("HC-05 disconnected.")
            status["arduino"] = "HC-05 disconnected"
            status["arduino_state"] = "wait"
            arduino_connected = False
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            was_connected = False

    # --- Cleanup on quit ---
    stop_volume()
    if ser and ser.is_open:
        try:
            ser.write(b"VOL0\n")
            ser.flush()
            ser.close()
        except Exception:
            pass


def main():
    pygame.init()
    W, H = 480, 330
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("SPARC Controller")

    def sysfont(size, bold=False):
        return pygame.font.SysFont("avenirnext,helveticaneue,helvetica,arial", size, bold=bold)

    title_font = sysfont(38, bold=True)
    sub_font = sysfont(13)
    label_font = sysfont(17, bold=True)
    status_font = sysfont(14)
    hint_font = sysfont(12)

    TEXT = (236, 238, 244)
    DIM = (135, 138, 152)
    CARD = (31, 33, 46)
    GREEN = (30, 215, 96)
    AMBER = (235, 170, 60)
    RED = (226, 85, 85)
    STATE_COLORS = {"ok": GREEN, "wait": RED, "err": RED}
    LOGO_BLUES = [(26, 54, 93), (37, 84, 146), (66, 122, 193), (120, 170, 220)]

    # Pre-rendered vertical gradient background
    bg = pygame.Surface((W, H))
    top, bottom = (24, 26, 38), (11, 11, 16)
    for y in range(H):
        f = y / H
        color = tuple(int(top[i] + (bottom[i] - top[i]) * f) for i in range(3))
        pygame.draw.line(bg, color, (0, y), (W, y))

    def fit_text(font, text, max_width):
        if font.size(text)[0] <= max_width:
            return text
        while text and font.size(text + "…")[0] > max_width:
            text = text[:-1]
        return text + "…"

    def draw_card(y, label, text, state, t):
        rect = pygame.Rect(24, y, W - 48, 52)
        pygame.draw.rect(screen, CARD, rect, border_radius=12)
        color = STATE_COLORS.get(state, AMBER)
        cy = y + 26
        # status dot with a soft pulse while waiting
        radius = 6 if state != "wait" else 5 + 1.5 * (0.5 + 0.5 * math.sin(t * 4))
        pygame.draw.circle(screen, tuple(c // 3 for c in color), (46, cy), int(radius) + 4)
        pygame.draw.circle(screen, color, (46, cy), int(radius))
        label_img = label_font.render(label, True, TEXT)
        screen.blit(label_img, (64, cy - label_img.get_height() // 2))
        text_img = status_font.render(fit_text(status_font, text, 270), True, DIM)
        screen.blit(text_img, (rect.right - 16 - text_img.get_width(), cy - text_img.get_height() // 2))

    status = {"spotify": "Connecting to Spotify...", "spotify_state": "wait",
              "arduino": "Not connected", "arduino_state": "wait"}
    stop_event = threading.Event()
    worker = threading.Thread(target=run_worker, args=(stop_event, status), daemon=True)
    worker.start()

    clock = pygame.time.Clock()
    t0 = time.time()
    running = True
    try:
        while running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False

            t = time.time() - t0
            connected = status["spotify_state"] == "ok" and status["arduino_state"] == "ok"
            screen.blit(bg, (0, 0))

            # Header: animated blue bars echoing the SPARC logo
            for i, blue in enumerate(LOGO_BLUES):
                bh = 14 + 18 * (0.5 + 0.5 * math.sin(t * (1.6 + 0.5 * i) + i * 1.3))
                pygame.draw.rect(screen, blue, (26 + i * 11, 66 - bh, 7, bh), border_radius=2)
            title_img = title_font.render("SPARC", True, TEXT)
            screen.blit(title_img, (78, 16))
            sub_img = sub_font.render("Spotify Proximity and Remote Control", True, DIM)
            screen.blit(sub_img, (80, 58))

            draw_card(92, "Spotify", status["spotify"], status["spotify_state"], t)
            draw_card(152, "Arduino", status["arduino"], status["arduino_state"], t)

            # Equalizer: dances in green when connected, red flatline when not
            eq_base, eq_max = 288, 60
            if connected:
                bars, bar_w, gap = 20, 14, 8
                for i in range(bars):
                    wave = 0.55 * (0.5 + 0.5 * math.sin(t * (2.0 + (i % 5) * 0.55) + i * 0.9))
                    wave += 0.45 * (0.5 + 0.5 * math.sin(t * 3.1 + i * 0.5))
                    bh = 8 + eq_max * wave
                    x = 24 + i * (bar_w + gap)
                    pygame.draw.rect(screen, GREEN, (x, eq_base - bh, bar_w, bh), border_radius=4)
            else:
                flatline_y = eq_base - 18
                pygame.draw.line(screen, (150, 70, 70), (24, flatline_y), (W - 24, flatline_y), 2)
                nc_img = label_font.render("NOT CONNECTED", True, RED)
                nc_img.set_alpha(int(160 + 95 * math.sin(t * 2.5)))
                screen.blit(nc_img, (W // 2 - nc_img.get_width() // 2, flatline_y - 36))

            hint_img = hint_font.render("Close this window to quit", True, (110, 112, 126))
            screen.blit(hint_img, (W // 2 - hint_img.get_width() // 2, H - 26))

            pygame.display.flip()
            clock.tick(30)
    except KeyboardInterrupt:
        pass

    print("\nExiting.")
    stop_event.set()
    worker.join(timeout=3)
    pygame.quit()


if __name__ == "__main__":
    main()