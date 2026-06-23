#include <SoftwareSerial.h>

// HC-05 wiring: HC-05 TX → pin 10, HC-05 RX → pin 11, HC-05 EN → pin 3
SoftwareSerial btSerial(10, 11);  // RX=10 (← HC-05 TX), TX=11 (→ HC-05 RX)

const byte triggerPin = 13;
const byte echoPin = 12;

const int ledPause = 4;

// Volume bar LEDs (5 LEDs)
const int volLeds[5] = { 5, 6, 7, 8, 9 };

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

float readDistanceCm() {
  digitalWrite(triggerPin, LOW);
  digitalWrite(triggerPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(triggerPin, LOW);
  long duration = pulseIn(echoPin, HIGH, 30000);
  if (duration == 0) return -1;
  return duration * 0.034 / 2.0;
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

// Maps vol (0-100) onto 5 LEDs
// 0      → all off
// 1-20   → 1 LED
// 21-40  → 2 LEDs
// 41-60  → 3 LEDs
// 61-80  → 4 LEDs
// 81-100 → 5 LEDs
void updateVolumeLEDs(int vol) {
  int bars = (vol == 0) ? 0 : ((vol - 1) / 20) + 1;
  for (int i = 0; i < 5; i++) {
    digitalWrite(volLeds[i], i < bars ? HIGH : LOW);
  }
}

void resetGestureState() {
  passCount = 0;
  volumeActive = false;
}

void setup() {
  Serial.begin(9600);
  btSerial.begin(9600);
  pinMode(3, OUTPUT);
  digitalWrite(3, LOW);  // EN LOW = normal data mode (HIGH would enter AT command mode)
  pinMode(triggerPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(ledPause, OUTPUT);
  for (int i = 0; i < 5; i++) {
    pinMode(volLeds[i], OUTPUT);
  }
}

void loop() {
  // Heartbeat-based connection detection: Python sends HB every ~2s.
  // If silent for HEARTBEAT_TIMEOUT ms, treat as disconnected and clear LEDs.
  bool nowConnected = (lastHeartbeat > 0) && (millis() - lastHeartbeat < HEARTBEAT_TIMEOUT);
  if (!nowConnected && btConnected) {
    updateVolumeLEDs(0);
    digitalWrite(ledPause, LOW);
    flashPin = -1;
    resetGestureState();
    handInZone = false;
    outOfRangeCount = 0;
    btConnected = false;
    Serial.println("BT disconnected");
  }

  // Read incoming messages from Python
  if (btSerial.available()) {
    String msg = btSerial.readStringUntil('\n');
    msg.trim();
    lastHeartbeat = millis();
    if (!btConnected) {
      btConnected = true;
      Serial.println("BT connected");
    }
    if (msg == "VS") {
      volumeActive = false;
    } else if (msg.startsWith("VOL")) {
      int vol = msg.substring(3).toInt();
      updateVolumeLEDs(vol);
    }
    // "HB" heartbeat messages only update lastHeartbeat (handled above)
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
        // Double pass
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
        // Too slow or wrong zone — reset as fresh first pass
        passCount = 1;
        passZone = handZone;
        firstPassExitTime = millis();
      }
    }
  }

  else if (inZone && handInZone) {
    // Check for hold
    if (!holdFired && millis() - handEntryTime >= HOLD_TIME) {
      btSerial.println("P");
      Serial.println("SENT: P");
      flashLed(ledPause);
      holdFired = true;
      resetGestureState();
    }
  }

  // Confirm single pass once double-pass window expires
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
