const byte triggerPin = 13;
const byte echoPin = 12;

const int ledSkip = 2;
const int ledVolume = 3;
const int ledPause = 4;

// Detection zones in cm
const float DETECT_MIN = 2.0;
const float DETECT_MAX = 30.0;
const float SWITCH_ZONE_MAX = 15.0;  // 2-15cm = switch mode, 15-30cm = volume mode

// Timing
const unsigned long HOLD_TIME = 3000;    // ms to hold still to enter a mode
const unsigned long PAUSE_WINDOW = 1000; // max ms from first wave exit to second wave exit
const unsigned long READ_INTERVAL = 25;  // sensor poll rate in ms

enum State { IDLE, HOLDING, SWITCH_MODE, VOLUME_MODE, PAUSE_PENDING };
State state = IDLE;

const int NUM_READINGS = 3;
float history[NUM_READINGS];
int idx = 0;

bool handInRange = false;
unsigned long lastReadTime = 0;
unsigned long handEnteredTime = 0;
unsigned long firstWaveExitTime = 0;

int outOfRangeCount = 0;
const int OUT_OF_RANGE_THRESHOLD = 3;

void clearModeLeds() {
  digitalWrite(ledSkip, LOW);
  digitalWrite(ledVolume, LOW);
}

void enterSwitchMode() {
  state = SWITCH_MODE;
  clearModeLeds();
  digitalWrite(ledSkip, HIGH);
  Serial.println("SWITCH_ON");
}

void enterVolumeMode() {
  state = VOLUME_MODE;
  clearModeLeds();
  digitalWrite(ledVolume, HIGH);
  Serial.println("VOLUME_ON");
}

float readDistanceCm() {
  digitalWrite(triggerPin, LOW);
  delayMicroseconds(2);
  digitalWrite(triggerPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(triggerPin, LOW);
  long duration = pulseIn(echoPin, HIGH, 25000); // 25ms timeout (~4m max)
  if (duration == 0) return -1;
  return duration * 0.034 / 2.0;
}

void setup() {
  Serial.begin(9600);
  pinMode(triggerPin, OUTPUT);
  pinMode(echoPin, INPUT);

  pinMode(ledSkip, OUTPUT);
  pinMode(ledVolume, OUTPUT);
  pinMode(ledPause, OUTPUT);

  for (int i = 0; i < NUM_READINGS; i++) history[i] = 0.0;
}

void loop() {
  if (millis() - lastReadTime < READ_INTERVAL) return;
  lastReadTime = millis();

  float current = readDistanceCm();

  history[idx] = current;
  idx = (idx + 1) % NUM_READINGS;

  bool inRange = (current >= DETECT_MIN && current <= DETECT_MAX);

  if (inRange) {
    outOfRangeCount = 0;
  } else {
    outOfRangeCount++;
  }
  bool handConfirmedGone = (!inRange && outOfRangeCount >= OUT_OF_RANGE_THRESHOLD);

  if (inRange && !handInRange) {
    handInRange = true;
    handEnteredTime = millis();

    if (state == IDLE) {
      state = HOLDING;
    }
    // If PAUSE_PENDING: second wave starting, handEnteredTime tracks it
  }

  else if (handConfirmedGone && handInRange) {
    handInRange = false;
    outOfRangeCount = 0;

    if (state == SWITCH_MODE) {
      clearModeLeds();
      Serial.println("SWITCH_OFF");
      state = IDLE;
    } else if (state == VOLUME_MODE) {
      clearModeLeds();
      Serial.println("VOLUME_OFF");
      state = IDLE;
    } else if (state == HOLDING) {
      // Quick exit = first wave
      firstWaveExitTime = millis();
      state = PAUSE_PENDING;
    } else if (state == PAUSE_PENDING) {
      // Second wave exit - trigger pause if within window
      if (millis() - firstWaveExitTime <= PAUSE_WINDOW) {
        Serial.println("P");
        digitalWrite(ledPause, !digitalRead(ledPause));
      }
      state = IDLE;
    }
  }

  else if (inRange && handInRange) {
    unsigned long holdDuration = millis() - handEnteredTime;

    if (state == PAUSE_PENDING) {
      if (millis() - firstWaveExitTime > PAUSE_WINDOW) {
        // Too slow between waves - treat as a fresh hold
        state = HOLDING;
      } else if (holdDuration >= HOLD_TIME) {
        // Second wave became a hold - enter mode instead
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
          handEnteredTime = millis(); // wrong zone, reset
        }
      }
    }
  }

  // Expire pause window when hand is out of range
  if (!handInRange && state == PAUSE_PENDING) {
    if (millis() - firstWaveExitTime > PAUSE_WINDOW) {
      state = IDLE;
    }
  }
}
