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
  btConnected = false;
  pinMode(3, OUTPUT);
  digitalWrite(3, LOW);
  pinMode(triggerPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(ledPause, OUTPUT);
  for (int i = 0; i < 5; i++) {
    pinMode(volLeds[i], OUTPUT);
  }
}

void loop() {
  bool nowConnected = (millis() - lastHeartbeat <= HEARTBEAT_TIMEOUT);
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
    } else if (msg == "HB") {
      btSerial.println("HB");  // ack so the app can confirm a live link
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