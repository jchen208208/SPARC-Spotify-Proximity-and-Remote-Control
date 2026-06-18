#include <HCSR04.h>

// Pins
byte triggerPin = 13;
byte echoPin = 12;

const int LED_SKIP = 2;
const int LED_VOLUME = 3;
const int LED_PAUSE = 4;
const int LED_UP = 5;
const int LED_DOWN = 6;

// Detection zones (cm)
const float ZONE_SKIP_MIN = 2.0;
const float ZONE_SKIP_MAX = 15.0;
const float ZONE_VOL_MIN = 15.0;
const float ZONE_VOL_MAX = 30.0;

// Timing (ms)
const unsigned long READ_INTERVAL = 50;
const unsigned long SWIFT_MAX_MS = 600;   // hand must leave within this for play/pause
const unsigned long SKIP_HOLD_MS = 1500;  // 1.5 seconds to arm skip mode
const unsigned long VOL_HOLD_MS = 3000;   // 3 seconds to arm volume mode
const unsigned long CMD_COOLDOWN_MS = 800;
const unsigned long VOL_COOLDOWN_MS = 200;
const unsigned long LED_FLASH_MS = 200;

// Minimum movement to fire a skip command
const float SKIP_MOVE_CM = 5.0;

// Volume mode — EMA smoothing + anchor-relative bucket control
const float EMA_ALPHA = 0.15;
const float VOL_BUCKET_CM = 3.0; // each 3cm from entry = one V+/V-

// State machine: IDLE -> WAITING -> SKIP_MODE or VOL_MODE -> IDLE
enum State { STATE_IDLE, STATE_WAITING, STATE_SKIP_MODE, STATE_VOL_MODE };
State currentState = STATE_IDLE;

// Rolling average buffer (5 samples, 250ms window)
const int NUM_READINGS = 5;
float history[NUM_READINGS];
int histIdx = 0;
int readingCount = 0;

// Require 3 consecutive in-range readings before accepting hand as present
const int CONFIRM_NEEDED = 3;
int confirmCount = 0;

// Timing state
unsigned long lastReadTime = 0;
unsigned long handEnteredAt = 0;
unsigned long lastCmdTime = 0;
unsigned long lastVolCmdTime = 0;

// Non-blocking LED flash state
int flashPin = -1;
unsigned long flashStartedAt = 0;

// Skip mode state
float skipAnchorDist = 0.0;
bool skipFired = false;

// Volume mode state
float volEma = 0.0;
float volAnchorDist = 0.0; // hand position when vol mode was entered
int volLastBucket = 0;
bool volEmaSeeded = false;


float smoothedDistance() {
    if (readingCount == 0) return 999.0;
    float sum = 0.0;
    int n = min(readingCount, NUM_READINGS);
    for (int i = 0; i < n; i++) sum += history[i];
    return sum / n;
}

void allLedsOff() {
    digitalWrite(LED_SKIP, LOW);
    analogWrite(LED_VOLUME, 0);
    digitalWrite(LED_PAUSE, LOW);
    digitalWrite(LED_UP, LOW);
    digitalWrite(LED_DOWN, LOW);
}

// Start a timed LED flash without blocking the loop
void flashLed(int pin) {
    flashPin = pin;
    flashStartedAt = millis();
    digitalWrite(pin, HIGH);
}

void updateLedFlash() {
    if (flashPin != -1 && millis() - flashStartedAt >= LED_FLASH_MS) {
        digitalWrite(flashPin, LOW);
        flashPin = -1;
    }
}

void setup() {
    Serial.begin(9600);
    HCSR04.begin(triggerPin, echoPin);
    pinMode(LED_SKIP, OUTPUT);
    pinMode(LED_VOLUME, OUTPUT);
    pinMode(LED_PAUSE, OUTPUT);
    pinMode(LED_UP, OUTPUT);
    pinMode(LED_DOWN, OUTPUT);
    for (int i = 0; i < NUM_READINGS; i++) history[i] = 0.0;
}

void loop() {
    if (millis() - lastReadTime < READ_INTERVAL) return;
    lastReadTime = millis();

    updateLedFlash();

    // Read sensor and update rolling average
    double* raw = HCSR04.measureDistanceCm();
    history[histIdx] = (float)raw[0];
    histIdx = (histIdx + 1) % NUM_READINGS;
    if (readingCount < NUM_READINGS) readingCount++;
    float current = smoothedDistance();

    // Classify zone
    bool inSkipZone = (current >= ZONE_SKIP_MIN && current <= ZONE_SKIP_MAX);
    bool inVolZone = (current > ZONE_VOL_MIN && current <= ZONE_VOL_MAX);
    bool inAnyZone = inSkipZone || inVolZone;

    // Require CONFIRM_NEEDED consecutive in-range readings before acting
    if (inAnyZone) { if (confirmCount < CONFIRM_NEEDED) confirmCount++; }
    else { confirmCount = 0; }
    bool handConfirmed = (confirmCount >= CONFIRM_NEEDED);

    switch (currentState) {

        case STATE_IDLE:
            if (handConfirmed) {
                currentState = STATE_WAITING;
                handEnteredAt = millis();
                skipAnchorDist = current;
                skipFired = false;
            }
            break;

        // Swift tap fires play/pause, sustained hold arms a mode
        case STATE_WAITING:
            if (!inAnyZone) {
                unsigned long heldFor = millis() - handEnteredAt;
                if (heldFor < SWIFT_MAX_MS && millis() - lastCmdTime > CMD_COOLDOWN_MS) {
                    Serial.println("P");
                    lastCmdTime = millis();
                    flashLed(LED_PAUSE);
                }
                currentState = STATE_IDLE;
            } else {
                unsigned long heldFor = millis() - handEnteredAt;
                if (inSkipZone && heldFor >= SKIP_HOLD_MS) {
                    currentState = STATE_SKIP_MODE;
                    skipAnchorDist = current;
                    skipFired = false;
                    allLedsOff();
                    digitalWrite(LED_SKIP, HIGH);
                } else if (inVolZone && heldFor >= VOL_HOLD_MS) {
                    currentState = STATE_VOL_MODE;
                    volEmaSeeded = false;
                    allLedsOff();
                    digitalWrite(LED_VOLUME, HIGH);
                    // Tell Python to read the current Spotify volume right now —
                    // that volume becomes the anchor for all V+/V- adjustments
                    Serial.println("V_START");
                }
            }
            break;

        // Skip armed — fire once on directional motion, re-arm after cooldown
        case STATE_SKIP_MODE:
            if (!inSkipZone) {
                currentState = STATE_IDLE;
                allLedsOff();
                break;
            }
            if (skipFired && millis() - lastCmdTime > CMD_COOLDOWN_MS) {
                skipFired = false;
                skipAnchorDist = current; // new baseline for next gesture
            }
            if (!skipFired) {
                float movement = skipAnchorDist - current; // positive = moved closer
                if (movement >= SKIP_MOVE_CM) {
                    Serial.println("S+");
                    lastCmdTime = millis();
                    skipFired = true;
                    flashLed(LED_UP);
                } else if (movement <= -SKIP_MOVE_CM) {
                    Serial.println("S-");
                    lastCmdTime = millis();
                    skipFired = true;
                    flashLed(LED_DOWN);
                }
            }
            break;

        // Volume mode — anchor-relative bucket control
        // Entry position = center. Each 3cm closer = V+, each 3cm away = V-.
        // Python uses V_START to know the real starting volume, so all adjustments
        // are relative to actual Spotify volume at the moment the user armed the mode.
        case STATE_VOL_MODE:
            if (!inVolZone) {
                currentState = STATE_IDLE;
                volEmaSeeded = false;
                allLedsOff();
                break;
            }
            {
                if (!volEmaSeeded) {
                    volEma = current;
                    volAnchorDist = current;
                    volLastBucket = 0;
                    volEmaSeeded = true;
                }

                volEma = EMA_ALPHA * current + (1.0 - EMA_ALPHA) * volEma;

                // Bucket relative to entry point — positive = moved closer = louder
                int bucket = (int)round((volAnchorDist - volEma) / VOL_BUCKET_CM);

                if (bucket != volLastBucket && millis() - lastVolCmdTime >= VOL_COOLDOWN_MS) {
                    if (bucket > volLastBucket) {
                        Serial.println("V+");
                        flashLed(LED_UP);
                        volLastBucket++;
                    } else {
                        Serial.println("V-");
                        flashLed(LED_DOWN);
                        volLastBucket--;
                    }
                    lastVolCmdTime = millis();
                    lastCmdTime = millis();
                }

                digitalWrite(LED_VOLUME, HIGH);
            }
            break;
    }
}