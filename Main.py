import time
import serial

SERIAL_PORT = "/dev/cu.usbmodem144302"
BAUD_RATE = 9600

MESSAGES = {
    "S+": "[ZONE 1] Next track",
    "S-": "[ZONE 1] Previous track",
    "V+": "[ZONE 2] Volume up (start)",
    "V-": "[ZONE 2] Volume down (start)",
    "P":  "[STOP] Stop volume / Toggle pause",
}

def main():
    print(f"Connecting to Arduino on {SERIAL_PORT}...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    ser.reset_input_buffer()
    print("Connected. Listening (Ctrl+C to quit)...\n")

    try:
        while True:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            msg = MESSAGES.get(line)
            if msg:
                print(msg)
            else:
                print(f"[RAW] {repr(line)}")
    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        ser.close()

if __name__ == "__main__":
    main()
