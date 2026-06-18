import time
import serial

SERIAL_PORT = "COM3"
BAUD_RATE = 9600

MESSAGES = {
    "SWITCH_ON":  "[MODE] Entered SWITCH mode",
    "SWITCH_OFF": "[MODE] Exited SWITCH mode",
    "VOLUME_ON":  "[MODE] Entered VOLUME mode",
    "VOLUME_OFF": "[MODE] Exited VOLUME mode",
    "P":          "[ACTION] Pause / Play",
}

def main():
    print(f"Connecting to Arduino on {SERIAL_PORT}...")
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
    time.sleep(2)
    ser.reset_input_buffer()  # discard startup garbage
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
