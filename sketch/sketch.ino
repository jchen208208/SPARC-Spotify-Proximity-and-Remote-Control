const byte triggerPin = 13;
const byte echoPin = 12;

const int ledSkip = 2;
const int ledVolume = 3;
const int ledPause = 4;

// Zone boundaries in cm
const float DETECT_MIN = 2.0;
const float ZONE1_MAX = 15.0;  // Zone 1: 2-15cm  → track control
const float ZONE2_MAX = 30.0;  // Zone 2: 15-30cm → volume control

// Gesture timing
const unsigned long HOLD_TIME = 1000;         // 1s hold triggers P
const unsigned long DOUBLE_PASS_WINDOW = 750; // ms window to complete a double pass
const unsigned long READ_INTERVAL = 25;

// 6 consecutive out-of-range readings (150ms) required to confirm hand is gone
const int OUT_OF_RANGE_THRESHOLD = 6;

// Non-blocking LED flash
int flashPin = -1;
unsigned long flashStart = 0;
const unsigned long FLASH_DURATION = 150;

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
  delayMicroseconds(2);
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

void setup() {
  Serial.begin(9600);
  pinMode(triggerPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(ledSkip, OUTPUT);
  pinMode(ledVolume, OUTPUT);
  pinMode(ledPause, OUTPUT);
}

void loop() {
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
          Serial.println("S-");
        } else {
          Serial.println("V-");
          volumeActive = true;
          digitalWrite(ledVolume, HIGH); // solid LED while volume ramping
        }
        if (passZone == 1) flashLed(ledSkip);
        passCount = 0;
      } else {
        // Too slow or wrong zone - fresh first pass
        passCount = 1;
        passZone = handZone;
        firstPassExitTime = millis();
      }
    }
  }

  else if (inZone && handInZone) {
    // Check for hold
    if (!holdFired && millis() - handEntryTime >= HOLD_TIME) {
      Serial.println("P");
      flashLed(ledPause);
      holdFired = true;
      passCount = 0;
      // P always clears volume state regardless of what Python does with it
      volumeActive = false;
      digitalWrite(ledVolume, LOW);
    }
  }

  // Confirm single pass once double-pass window expires
  if (!handInZone && passCount == 1 && !volumeActive && millis() - firstPassExitTime > DOUBLE_PASS_WINDOW) {
    if (passZone == 1) {
      Serial.println("S+");
      flashLed(ledSkip);
    } else {
      Serial.println("V+");
      volumeActive = true;
      digitalWrite(ledVolume, HIGH);
    }
    passCount = 0;
  }
}
