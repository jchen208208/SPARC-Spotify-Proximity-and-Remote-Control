#include "BluetoothSerial.h"
BluetoothSerial btSerial;

#include <Wire.h>
#include "Adafruit_VL53L0X.h"
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

const int ledPause = 4;

// Zone boundaries in cm
const float DETECT_MIN = 2.0;
const float ZONE1_MAX = 15.0;  // Zone 1: 2-15cm  → track control
const float ZONE2_MAX = 30.0;  // Zone 2: 15-30cm → volume control

// Gesture timing
const unsigned long HOLD_TIME = 300;
const unsigned long DOUBLE_PASS_WINDOW = 800;
const unsigned long READ_INTERVAL = 25;

// 6 consecutive out-of-range readings (150ms) required to confirm hand is gone
const int OUT_OF_RANGE_THRESHOLD = 6;

// Non-blocking LED flash
int flashPin = -1;
unsigned long flashStart = 0;
const unsigned long FLASH_DURATION = 150;

// Heartbeat: Python sends "HB\n" every ~2s; if absent for 5s, go dark
const unsigned long HEARTBEAT_TIMEOUT = 5000;
unsigned long lastHeartbeat = 0;
bool btConnected = false;

bool handInZone = false;
int handZone = 0;
unsigned long handEntryTime = 0;
bool holdFired = false;

int passCount = 0;
int passZone = 0;
unsigned long firstPassExitTime = 0;

// When volume is actively ramping, block all pass gestures
bool volumeActive = false;

int outOfRangeCount = 0;
unsigned long lastReadTime = 0;

// False if the VL53L0X never came up. Gestures stop working, but Bluetooth keeps
// running so the app still connects and says so, rather than the board going dark.
bool sensorReady = false;

float readDistanceCm() {
  if (!sensorReady) return -1;             // never poke a sensor that isn't there
  VL53L0X_RangingMeasurementData_t measure;
  lox.rangingTest(&measure, false);
  if (measure.RangeStatus == 4) return -1; // out of range / invalid
  return measure.RangeMilliMeter / 10.0;   // mm → cm
}

void flashLed(int pin) {
  if (flashPin >= 0) digitalWrite(flashPin, LOW);
  digitalWrite(pin, HIGH);
  flashPin = pin;
  flashStart = millis();
}

void handleFlash() {
  if (flashPin >= 0 && millis() - flashStart >= FLASH_DURATION) {
    digitalWrite(flashPin, LOW);
    flashPin = -1;
  }
}


void resetGestureState() {
  passCount = 0;
  volumeActive = false;
}

void setup() {
  // Serial first: with Serial.begin() after lox.begin(), a sensor that doesn't
  // answer left the board dark with no clue why - Bluetooth was already
  // advertising by then, so it looked alive from the app while loop() never ran.
  Serial.begin(9600);
  Serial.println("SPARC booting");

  btSerial.begin("SPARC");

  Wire.begin(21, 22); // SDA, SCL

  // On a cold power-up the VL53L0X shares the ESP32's rail and is still booting
  // when we get here, so an immediate begin() finds nothing - and the old sketch
  // ignored the result, wedging setup() so Bluetooth never answered again. An
  // EN-reset or a reflash hid this, because the sensor was already powered by
  // then; that's why it only ever failed on a physical replug. Wait, retry, and
  // carry on either way so a dead sensor can't take Bluetooth down with it.
  delay(200);
  for (int i = 0; i < 5 && !sensorReady; i++) {
    sensorReady = lox.begin();
    if (!sensorReady) delay(200);
  }
  Serial.println(sensorReady
                   ? "VL53L0X ready"
                   : "VL53L0X NOT FOUND - check wiring (SDA=21, SCL=22, 3V3, GND)");

  btConnected = false;

  pinMode(ledPause, OUTPUT);

  Serial.println("setup done");
}

void loop() {
  bool nowConnected = (millis() - lastHeartbeat <= HEARTBEAT_TIMEOUT);
  if (!nowConnected && btConnected) {
    digitalWrite(ledPause, LOW);
    flashPin = -1;
    resetGestureState();
    handInZone = false;
    outOfRangeCount = 0;
    btConnected = false;
    Serial.println("BT disconnected");
  }

  if (btSerial.available()) {
    String msg = btSerial.readStringUntil('\n');
    msg.trim();
    lastHeartbeat = millis();
    if (!btConnected) {
      btConnected = true;
      Serial.println("BT connected");
    }
    if (msg == "HB") {
    btSerial.println("ACK");
    } else if (msg == "VS") {
      volumeActive = false;
    } else if (msg.startsWith("VOL")) {
      int vol = msg.substring(3).toInt();
    }
  }

  if (!btConnected) return;

  if (millis() - lastReadTime < READ_INTERVAL) return;
  lastReadTime = millis();

  handleFlash();

  float current = readDistanceCm();
  bool inZone = (current >= DETECT_MIN && current <= ZONE2_MAX);

  if (inZone) {
    outOfRangeCount = 0;
  } else {
    outOfRangeCount++;
  }
  bool handConfirmedGone = (!inZone && outOfRangeCount >= OUT_OF_RANGE_THRESHOLD);

  if (inZone && !handInZone) {
    handInZone = true;
    handEntryTime = millis();
    holdFired = false;
    handZone = (current <= ZONE1_MAX) ? 1 : 2;
  }

  else if (handConfirmedGone && handInZone) {
    handInZone = false;
    outOfRangeCount = 0;

    if (!holdFired && !volumeActive) {
      if (passCount == 0) {
        passCount = 1;
        passZone = handZone;
        firstPassExitTime = millis();
      } else if (passCount == 1 && handZone == passZone && millis() - firstPassExitTime <= DOUBLE_PASS_WINDOW) {
        if (passZone == 1) {
          btSerial.println("S-");
          Serial.println("SENT: S-");
        } else {
          btSerial.println("V-");
          Serial.println("SENT: V-");
          volumeActive = true;
        }
        passCount = 0;
      } else {
        passCount = 1;
        passZone = handZone;
        firstPassExitTime = millis();
      }
    }
  }

  else if (inZone && handInZone) {
    if (!holdFired && millis() - handEntryTime >= HOLD_TIME) {
      btSerial.println("P");
      Serial.println("SENT: P");
      flashLed(ledPause);
      holdFired = true;
      resetGestureState();
    }
  }

  if (!handInZone && passCount == 1 && !volumeActive && millis() - firstPassExitTime > DOUBLE_PASS_WINDOW) {
    if (passZone == 1) {
      btSerial.println("S+");
      Serial.println("SENT: S+");
    } else {
      btSerial.println("V+");
      Serial.println("SENT: V+");
      volumeActive = true;
    }
    passCount = 0;
  }
}