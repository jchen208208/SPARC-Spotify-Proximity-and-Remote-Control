#include <HCSR04.h>

const byte triggerPin = 13;
const byte echoPin = 12;

const int ledSkip = 2;
const int ledVolume = 3;
const int ledPause = 4;
const int ledUp = 5;
const int ledDown = 6;

// Detection zones in cm
const float DETECT_MIN = 2.0;
const float DETECT_MAX = 30.0;
const float SWITCH_ZONE_MAX = 15.0;  // 2-15cm = switch mode, 15-30cm = volume mode

// Timing
const unsigned long HOLD_TIME = 3000;       // ms to hold still to enter a mode
const unsigned long PAUSE_WINDOW = 1000;    // max ms from first wave exit to second wave exit
const unsigned long READ_INTERVAL = 50;     // sensor poll rate in ms
const unsigned long GESTURE_COOLDOWN = 500; // min ms between S+/S-/V+/V- outputs

// cm/reading needed to count as intentional movement
const float MOVE_THRESH = 6.0;

// Each state is fully isolated - no mode bleeds into another
enum State { IDLE, HOLDING, SWITCH_MODE, VOLUME_MODE, PAUSE_PENDING };
State state = IDLE;

const int NUM_READINGS = 3;
double history[NUM_READINGS];
int idx = 0;

bool handInRange = false;
unsigned long lastReadTime = 0;
unsigned long handEnteredTime = 0;
unsigned long firstWaveExitTime = 0;
unsigned long lastGestureTime = 0;

// Debounce - require consecutive out-of-range readings before treating hand as gone
int outOfRangeCount = 0;
const int OUT_OF_RANGE_THRESHOLD = 3;

void clearModeLeds() {
  digitalWrite(ledSkip, LOW);
  digitalWrite(ledVolume, LOW);
  digitalWrite(ledUp, LOW);
  digitalWrite(ledDown, LOW);
}

void enterSwitchMode() {
  state = SWITCH_MODE;
  clearModeLeds();
  digitalWrite(ledSkip, HIGH);
  // Reset timers so entry movement doesn't immediately fire S+/S-
  handEnteredTime = millis();
  lastGestureTime = millis();
}

void enterVolumeMode() {
  state = VOLUME_MODE;
  clearModeLeds();
  digitalWrite(ledVolume, HIGH);
  handEnteredTime = millis();
  lastGestureTime = millis();
}

void setup() {
  Serial.begin(9600);
  HCSR04.begin(triggerPin, echoPin);

  pinMode(ledSkip, OUTPUT);
  pinMode(ledVolume, OUTPUT);
  pinMode(ledUp, OUTPUT);
  pinMode(ledDown, OUTPUT);
  pinMode(ledPause, OUTPUT);

  for (int i = 0; i < NUM_READINGS; i++) history[i] = 0.0;
}

void loop() {
  if (millis() - lastReadTime < READ_INTERVAL) return;
  lastReadTime = millis();

  double* distances = HCSR04.measureDistanceCm();
  double current = distances[0];

  double previous = history[(idx - 1 + NUM_READINGS) % NUM_READINGS];
  history[idx] = current;
  idx = (idx + 1) % NUM_READINGS;

  bool inRange = (current >= DETECT_MIN && current <= DETECT_MAX);
  double change = current - previous;

  // Debounce: require several consecutive out-of-range readings
  if (inRange) {
    outOfRangeCount = 0;
  } else {
    outOfRangeCount++;
  }
  bool handConfirmedGone = (!inRange && outOfRangeCount >= OUT_OF_RANGE_THRESHOLD);

  if (inRange && !handInRange) {
    // Hand just entered detection zone
    handInRange = true;
    handEnteredTime = millis();

    if (state == IDLE) {
      state = HOLDING;
    }
    // If PAUSE_PENDING: second wave is starting, handEnteredTime tracks it
  }

  else if (handConfirmedGone && handInRange) {
    // Hand just left detection zone (confirmed, not noise)
    handInRange = false;
    outOfRangeCount = 0;

    if (state == SWITCH_MODE || state == VOLUME_MODE) {
      // Clean mode exit - never falls into pause logic
      clearModeLeds();
      state = IDLE;
    }
    else if (state == HOLDING) {
      // Quick exit before 3s = first wave of double-wave pause gesture
      firstWaveExitTime = millis();
      state = PAUSE_PENDING;
    }
    else if (state == PAUSE_PENDING) {
      // Second wave exit - trigger pause only if within the time window
      if (millis() - firstWaveExitTime <= PAUSE_WINDOW) {
        Serial.println("P");
        digitalWrite(ledPause, !digitalRead(ledPause));
      }
      state = IDLE;
    }
  }

  else if (inRange && handInRange) {
    // Hand still in detection zone
    unsigned long holdDuration = millis() - handEnteredTime;

    if (state == PAUSE_PENDING) {
      // Second wave is in progress - check if it turned into a hold or window expired
      if (millis() - firstWaveExitTime > PAUSE_WINDOW) {
        // Too slow between waves - downgrade to a normal hold
        state = HOLDING;
      } else if (holdDuration >= HOLD_TIME) {
        // Held too long during second wave - enter a mode instead of triggering pause
        if (current < SWITCH_ZONE_MAX) {
          enterSwitchMode();
        } else {
          enterVolumeMode();
        }
      }
    }

    else if (state == HOLDING) {
      if (holdDuration >= HOLD_TIME) {
        if (current >= DETECT_MIN && current < SWITCH_ZONE_MAX) {
          enterSwitchMode();
        } else if (current >= SWITCH_ZONE_MAX && current <= DETECT_MAX) {
          enterVolumeMode();
        } else {
          handEnteredTime = millis(); // wrong zone, reset and keep waiting
        }
      }
    }

    // Rate-limited movement detection for active modes only
    if (state == SWITCH_MODE && millis() - lastGestureTime > GESTURE_COOLDOWN) {
      if (change > MOVE_THRESH) {
        Serial.println("S+");
        digitalWrite(ledUp, HIGH);
        digitalWrite(ledDown, LOW);
        lastGestureTime = millis();
      } else if (change < -MOVE_THRESH) {
        Serial.println("S-");
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, HIGH);
        lastGestureTime = millis();
      } else {
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, LOW);
      }
    }

    if (state == VOLUME_MODE && millis() - lastGestureTime > GESTURE_COOLDOWN) {
      if (change > MOVE_THRESH) {
        Serial.println("V+");
        digitalWrite(ledUp, HIGH);
        digitalWrite(ledDown, LOW);
        lastGestureTime = millis();
      } else if (change < -MOVE_THRESH) {
        Serial.println("V-");
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, HIGH);
        lastGestureTime = millis();
      } else {
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, LOW);
      }
    }
  }

  // Expire the pause window when hand is out of range
  if (!handInRange && state == PAUSE_PENDING) {
    if (millis() - firstWaveExitTime > PAUSE_WINDOW) {
      state = IDLE;
    }
  }
}
