// SPARC as a BLE HID media remote - no Spotify API, no companion app.
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>

#include <Wire.h>
#include "Adafruit_VL53L0X.h"
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

#include <FastLED.h>

#define LED_PIN 18
#define NUMPIXELS 8

CRGB leds[NUMPIXELS];

const int ledPause = 4;

// Consumer Control only - deliberately NO keyboard usage page. iOS hides the
// on-screen keyboard system-wide for anything that declares itself a keyboard,
// which would stop the user typing in every app while SPARC is connected.
static const uint8_t REPORT_ID = 1;
static uint8_t reportMap[] = {
  0x05, 0x0C,        // Usage Page (Consumer)
  0x09, 0x01,        // Usage (Consumer Control)
  0xA1, 0x01,        // Collection (Application)
  0x85, REPORT_ID,   //   Report ID (1)
  0x15, 0x00,        //   Logical Minimum (0)
  0x25, 0x01,        //   Logical Maximum (1)
  0x75, 0x01,        //   Report Size (1)
  0x95, 0x07,        //   Report Count (7)
  0x09, 0xB5,        //   Scan Next Track      -> bit 0
  0x09, 0xB6,        //   Scan Previous Track  -> bit 1
  0x09, 0xB7,        //   Stop                 -> bit 2
  0x09, 0xCD,        //   Play/Pause           -> bit 3
  0x09, 0xE2,        //   Mute                 -> bit 4
  0x09, 0xE9,        //   Volume Increment     -> bit 5
  0x09, 0xEA,        //   Volume Decrement     -> bit 6
  0x81, 0x02,        //   Input (Data, Variable, Absolute)
  0x95, 0x01,        //   Report Count (1)
  0x81, 0x03,        //   Input (Constant) - pad to a whole byte
  0xC0               // End Collection
};

const uint8_t KEY_NEXT       = 1 << 0;
const uint8_t KEY_PREV       = 1 << 1;
const uint8_t KEY_PLAY_PAUSE = 1 << 3;
const uint8_t KEY_VOL_UP     = 1 << 5;
const uint8_t KEY_VOL_DOWN   = 1 << 6;

// A zero-length press is ignored by some hosts, so hold the bit briefly.
const unsigned long KEY_HOLD_MS = 15;

NimBLEHIDDevice *hid = nullptr;
NimBLECharacteristic *inputReport = nullptr;
NimBLEServer *pServer = nullptr;
volatile bool bleConnected = false;
volatile bool authDone = false;
volatile uint16_t connHandle = 0;
bool wasConnected = false;
char peerAddr[24] = "";

// Apple Media Service: iOS pushes now-playing state to accessories over GATT - it's
// what Apple Watch uses. Unlike a volume estimate this is a closed loop, since every
// play/pause/seek re-anchors it, so drift can't accumulate. iOS only; on anything
// else discovery just fails and the strip stays in gesture-animation mode.
static const char *AMS_SERVICE       = "89D3502B-0F36-433A-8EF4-C502AD55F8DC";
static const char *AMS_ENTITY_UPDATE = "2F7CABCE-808D-411F-9A0C-BB92BA96C102";

bool amsFound = false;
int amsTries = 0;
unsigned long amsNextTry = 0;

float amsElapsed = 0;    // seconds into the track, as of amsAnchor
float amsRate = 1.0;
float amsDuration = 0;
int amsState = 0;        // 0 paused, 1 playing, 2 rewinding, 3 fast-forwarding
unsigned long amsAnchor = 0;

// Zone boundaries in cm. Auto-calibrated: whatever's parked within 1m of the
// sensor for 5+ seconds becomes the "back wall," and the space in front of it
// splits evenly into zone 1 (near, track control) and zone 2 (far, volume).
// Falls back to a fixed 15/30cm split when nothing is sitting in range.
const float DETECT_MIN = 2.0;
const float DEFAULT_ZONE1_MAX = 15.0;
const float DEFAULT_ZONE2_MAX = 30.0;
float zone1Max = DEFAULT_ZONE1_MAX;  // Zone 1: DETECT_MIN..zone1Max → track control
float zone2Max = DEFAULT_ZONE2_MAX;  // Zone 2: zone1Max..zone2Max  → volume control

// Calibration tuning
const float CALIBRATION_RANGE = 100.0;          // only consider objects within 1m
const float CALIBRATION_MIN = 10.0;             // ignore stable reads closer than this - more likely a resting hand than a backdrop
const unsigned long CALIBRATION_HOLD_MS = 5000; // must sit still this long to lock in
const float CALIBRATION_TOLERANCE = 3.0;        // cm of wobble still counted as "the same object"
const unsigned long CALIB_LOSS_MS = 1000;       // debounce before reverting to default
const float CALIBRATION_MARGIN = 0.2;           // fraction of the backdrop distance kept clear above zone 2

float calibRefDist = -1;
unsigned long calibStableSince = 0;
unsigned long calibNoObjectSince = 0;
bool isCalibrated = false;

// Gesture timing
const unsigned long HOLD_TIME = 300;
const unsigned long DOUBLE_PASS_WINDOW = 800;

// Time, not sample count: the VL53L0X's blocking read paces the loop, so a fixed
// number of samples drifts with the sensor's timing budget.
// Long enough to ride out a dropout mid-wave, short enough that two quick waves are
// still seen as two passes rather than one continuous hold.
const unsigned long OUT_OF_RANGE_MS = 100;

// Non-blocking LED flash
int flashPin = -1;
unsigned long flashStart = 0;
const unsigned long FLASH_DURATION = 150;

bool handInZone = false;
int handZone = 0;
unsigned long handEntryTime = 0;
bool holdFired = false;

// Tracked across a whole pass so the zone is decided by closest approach, not by
// whatever single reading happened to arrive first.
float passMinDist = 999;

int passCount = 0;
int passZone = 0;
unsigned long firstPassExitTime = 0;

// When volume is actively ramping, block all pass gestures
bool volumeActive = false;
bool volumeUp = false;
unsigned long lastVolumeTick = 0;
const unsigned long VOLUME_INTERVAL = 200;

// The strip can't show the host's volume - nothing comes back over HID to read, and
// an open-loop estimate desyncs the moment the user touches the volume themselves.
// It confirms gestures instead, which needs no host state and so is never wrong.
enum Anim { ANIM_NONE, ANIM_NEXT, ANIM_PREV, ANIM_PULSE, ANIM_VOL_UP, ANIM_VOL_DN };
Anim anim = ANIM_NONE;
unsigned long animStart = 0;
const unsigned long ANIM_STEP = 45;   // ms per LED
const unsigned long PULSE_MS = 320;
const unsigned long FRAME_MS = 20;    // cap redraws; WS2812B writes are not free
unsigned long lastFrame = 0;

// Nothing tells us when the host hits max/min, so bound the ramp ourselves.
// macOS and iOS cover the full range in 16 presses.
int volumeSteps = 0;
const int MAX_VOLUME_STEPS = 20;

unsigned long outOfRangeSince = 0;

// Debug: periodic Serial dump of raw distance + current zone state.
const unsigned long DEBUG_PRINT_INTERVAL = 200; // ms between prints - 5/sec is readable, doesn't flood
unsigned long lastDebugPrint = 0;

// False if the VL53L0X never came up. Gestures stop working, but Bluetooth keeps
// running so the board still pairs and looks alive, rather than going dark.
bool sensorReady = false;

float readDistanceCm() {
  if (!sensorReady) return -1;             // never poke a sensor that isn't there
  VL53L0X_RangingMeasurementData_t measure;
  lox.rangingTest(&measure, false);
  if (measure.RangeStatus != 0) return -1; // 0 is the only valid status; 1,2,3,5 return garbage
  return measure.RangeMilliMeter / 10.0;   // mm → cm
}

// Watches for something parked within 1m of the sensor. If it holds still long
// enough, that becomes the new outer edge of the gesture range, split evenly
// into zone 1 (near) and zone 2 (far). A hand gesturing through never holds
// still for the full 5 seconds - its distance keeps changing, which restarts
// the clock below - so normal use can't trigger this, only something left
// sitting in front of the sensor (a monitor stand, a wall, a mug).
void updateCalibration(float current) {
  bool objectPresent = (current >= DETECT_MIN && current <= CALIBRATION_RANGE);

  if (!objectPresent) {
    // Nothing within 1m - debounce briefly (a single dropped sample shouldn't
    // undo a calibration), then fall back to the fixed default zones.
    if (calibNoObjectSince == 0) calibNoObjectSince = millis();
    if (isCalibrated && millis() - calibNoObjectSince >= CALIB_LOSS_MS) {
      zone1Max = DEFAULT_ZONE1_MAX;
      zone2Max = DEFAULT_ZONE2_MAX;
      isCalibrated = false;
      Serial.println("No object within 1m - zones reset to default 15/30cm");
    }
    calibRefDist = -1;
    calibStableSince = 0;
    return;
  }
  calibNoObjectSince = 0;

  float diff = current - calibRefDist;
  if (diff < 0) diff = -diff;

  if (calibRefDist < 0 || diff > CALIBRATION_TOLERANCE) {
    // First sighting, or it moved enough that this isn't the same dwell - restart the clock.
    calibRefDist = current;
    calibStableSince = millis();
    return;
  }

  calibRefDist = (calibRefDist * 0.9f) + (current * 0.1f); // smooth out sensor jitter

  if (millis() - calibStableSince >= CALIBRATION_HOLD_MS && calibRefDist >= CALIBRATION_MIN) {
    // Stop the gesture range short of the backdrop. Parking zone2Max on the object
    // itself put it right on the inclusive edge of inZone, so sensor jitter around
    // calibRefDist read as a hand entering and leaving zone 2 with nothing there -
    // spurious volume ramps and play/pause. The margin scales with distance because
    // so does the noise.
    // Never less than the wobble we already tolerate as "the same object", or a
    // close backdrop would sit back inside the zone again.
    float margin = calibRefDist * CALIBRATION_MARGIN;
    if (margin < CALIBRATION_TOLERANCE) margin = CALIBRATION_TOLERANCE;
    zone2Max = calibRefDist - margin;
    zone1Max = zone2Max / 2.0f;
    if (!isCalibrated) {
      isCalibrated = true;
      Serial.print("Zones calibrated to object at ");
      Serial.print(calibRefDist);
      Serial.println("cm");
    }
  }
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

// pos 0..1 along the strip. SPARC blue: light blue at the start → dark blue at the end.
CRGB barColour(float pos) {
  int r = map(pos * 100, 0, 100, 120,   0);
  int g = map(pos * 100, 0, 100, 200,   0);
  int b = map(pos * 100, 0, 100, 255, 120);
  return CRGB(r, g, b);
}

void stripOff() {
  FastLED.clear();
  FastLED.show();
}

// AMS only pushes on change, so interpolate between updates for a smooth bar.
void renderProgress() {
  if (amsDuration < 1.0) return; // no track loaded yet

  float pos = amsElapsed;
  if (amsState == 1) pos += (millis() - amsAnchor) / 1000.0f * amsRate;
  float frac = constrain(pos / amsDuration, 0.0f, 1.0f);

  float lit = frac * NUMPIXELS;
  int full = (int)lit;
  uint8_t partial = (uint8_t)((lit - full) * 255);

  for (int i = 0; i < NUMPIXELS; i++) {
    leds[i] = barColour((float)i / (NUMPIXELS - 1));
    if (i > full)       leds[i] = CRGB::Black;
    else if (i == full) leds[i].nscale8(partial); // part-lit head keeps 8 pixels smooth
  }
  FastLED.show();
}

void startAnim(Anim a) {
  anim = a;
  animStart = millis();
}

// Non-blocking: called every loop, draws one frame and clears itself when done.
void renderAnim() {
  if (anim == ANIM_NONE) return;
  unsigned long t = millis() - animStart;

  if (anim == ANIM_PULSE) {
    if (t >= PULSE_MS) { anim = ANIM_NONE; stripOff(); return; }
    uint8_t fade = 255 - (t * 255 / PULSE_MS);
    for (int i = 0; i < NUMPIXELS; i++) {
      leds[i] = barColour((float)i / (NUMPIXELS - 1));
      leds[i].nscale8(fade);
    }
    FastLED.show();
    return;
  }

  unsigned long frame = t / ANIM_STEP;
  bool looping = (anim == ANIM_VOL_UP || anim == ANIM_VOL_DN);
  if (!looping && frame >= NUMPIXELS) { anim = ANIM_NONE; stripOff(); return; }

  int head = frame % NUMPIXELS;
  if (anim == ANIM_PREV || anim == ANIM_VOL_DN) head = NUMPIXELS - 1 - head;

  for (int i = 0; i < NUMPIXELS; i++) {
    int behind = (anim == ANIM_PREV || anim == ANIM_VOL_DN) ? i - head : head - i;
    if (behind >= 0 && behind < 3) {          // lit head plus a short tail
      leds[i] = barColour((float)i / (NUMPIXELS - 1));
      leds[i].nscale8(255 >> (behind * 2));
    } else {
      leds[i] = CRGB::Black;
    }
  }
  FastLED.show();
}

// Press and release. Leaving a bit set reads as a stuck key - for volume the
// host would auto-repeat forever.
void sendMediaKey(uint8_t mask, const char *label) {
  if (!bleConnected || inputReport == nullptr) return;

  uint8_t v = mask;
  inputReport->setValue(&v, 1);
  inputReport->notify();
  delay(KEY_HOLD_MS);
  v = 0;
  inputReport->setValue(&v, 1);
  inputReport->notify();

  Serial.print("SENT: ");
  Serial.println(label);
}

// Notification payload: [EntityID][AttributeID][flags][value as UTF-8].
void amsNotify(NimBLERemoteCharacteristic *chr, uint8_t *data, size_t len, bool isNotify) {
  if (len < 3) return;

  char buf[48];
  size_t n = len - 3;
  if (n >= sizeof(buf)) n = sizeof(buf) - 1;
  memcpy(buf, data + 3, n);
  buf[n] = '\0';

  if (data[0] == 0 && data[1] == 1) {        // Player / PlaybackInfo
    // "state,rate,elapsed" - any field may arrive empty, so keep the last known value.
    char *f[3] = {buf, nullptr, nullptr};
    int nf = 1;
    for (char *c = buf; *c && nf < 3; c++) {
      if (*c == ',') { *c = '\0'; f[nf++] = c + 1; }
    }
    if (f[0] && *f[0]) amsState   = atoi(f[0]);
    if (f[1] && *f[1]) amsRate    = atof(f[1]);
    if (f[2] && *f[2]) amsElapsed = atof(f[2]);
    amsAnchor = millis();
  } else if (data[0] == 2 && data[1] == 3) { // Track / Duration
    amsDuration = atof(buf);
  }
}

// We're the peripheral, but GATT is symmetric - NimBLEServer::getClient() hands us a
// client for the inbound connection so we can read services on the phone.
void setupAMS() {
  NimBLEClient *client = pServer->getClient(connHandle);
  if (!client) return;

  NimBLERemoteService *svc = client->getService(AMS_SERVICE);
  if (!svc) {
    Serial.println("AMS not offered - gesture animations only");
    return;
  }

  NimBLERemoteCharacteristic *eu = svc->getCharacteristic(AMS_ENTITY_UPDATE);
  if (!eu || !eu->subscribe(true, amsNotify)) {
    Serial.println("AMS entity update subscribe failed");
    return;
  }

  const uint8_t wantPlayer[] = {0, 1}; // Player -> PlaybackInfo (state, rate, elapsed)
  const uint8_t wantTrack[]  = {2, 3}; // Track  -> Duration
  eu->writeValue(wantPlayer, sizeof(wantPlayer), true);
  eu->writeValue(wantTrack, sizeof(wantTrack), true);

  amsFound = true;
  Serial.println("AMS connected - progress bar active");
}

// Only flags state - the LED teardown happens in loop() so FastLED is never
// driven from the BLE task.
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *server, NimBLEConnInfo &info) override {
    // Identity address, not getAddress(): Apple devices rotate their over-the-air
    // address every few minutes, so that one makes a returning host look brand new.
    strncpy(peerAddr, info.getIdAddress().toString().c_str(), sizeof(peerAddr) - 1);
    peerAddr[sizeof(peerAddr) - 1] = '\0';
    connHandle = info.getConnHandle();
    bleConnected = true;
  }
  void onDisconnect(NimBLEServer *server, NimBLEConnInfo &info, int reason) override {
    bleConnected = false;
    authDone = false;
    amsFound = false;
    amsTries = 0;
    amsDuration = 0;
    NimBLEDevice::startAdvertising(); // or it never comes back
  }
  // iOS won't expose AMS until the link is encrypted, so don't probe before bonding.
  void onAuthenticationComplete(NimBLEConnInfo &info) override {
    authDone = true;
    amsNextTry = 0;
  }
};

void resetGestureState() {
  passCount = 0;
  volumeActive = false;
}

void setup() {
  // Serial first: with Serial.begin() after lox.begin(), a sensor that doesn't
  // answer left the board dark with no clue why.
  Serial.begin(9600);
  Serial.println("SPARC booting (BLE HID)");

  NimBLEDevice::init("SPARC");
  NimBLEDevice::setSecurityAuth(true, false, true); // bonding, no MITM, secure connections
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);

  pServer = NimBLEDevice::createServer();
  NimBLEServer *server = pServer;
  server->setCallbacks(new ServerCallbacks());

  hid = new NimBLEHIDDevice(server);
  inputReport = hid->getInputReport(REPORT_ID);
  hid->setManufacturer("SPARC");
  hid->setPnp(0x02, 0x303A, 0x0001, 0x0100);
  hid->setHidInfo(0x00, 0x01);
  hid->setReportMap(reportMap, sizeof(reportMap));
  hid->setBatteryLevel(100);
  server->start();

  // GENERIC_HID, not HID_KEYBOARD - see the report map note above.
  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->setAppearance(GENERIC_HID);
  adv->addServiceUUID(hid->getHidService()->getUUID());
  adv->enableScanResponse(true);
  adv->start();

  Wire.begin(21, 22); // SDA, SCL

  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUMPIXELS);
  FastLED.setBrightness(10);
  FastLED.clear();
  FastLED.show();


  // On a cold power-up the VL53L0X shares the ESP32's rail and is still booting
  // when we get here, so an immediate begin() finds nothing. Wait, retry, and
  // carry on either way so a dead sensor can't take Bluetooth down with it.
  delay(200);
  for (int i = 0; i < 5 && !sensorReady; i++) {
    sensorReady = lox.begin();
    if (!sensorReady) delay(200);
  }
  // Gestures used to only need 2-30cm, so a short timing budget traded range for
  // speed. Calibration now needs to see out to 1m, and skin/clothing reflect IR
  // far worse than a flat wall - the old high-speed config was losing signal
  // (and returning invalid RangeStatus) well before that. Long-range mode lowers
  // the signal-rate threshold to buy back distance.
  // The budget is still pinned back down afterwards (configSensor sets its own):
  // long-range's default is slow enough that a fast wave lands in the gaps between
  // samples again, which is the bug the 20ms budget was here to fix.
  if (sensorReady) {
    lox.configSensor(Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE);
    lox.setMeasurementTimingBudgetMicroSeconds(20000);
  }

  Serial.println(sensorReady
                   ? "VL53L0X ready"
                   : "VL53L0X NOT FOUND - check wiring (SDA=21, SCL=22, 3V3, GND)");

  pinMode(ledPause, OUTPUT);

  Serial.println("setup done - advertising as SPARC");
}

void loop() {
  if (bleConnected && !wasConnected) {
    wasConnected = true;
    Serial.print("BLE connected: ");
    Serial.println(peerAddr);
  } else if (!bleConnected && wasConnected) {
    digitalWrite(ledPause, LOW);
    anim = ANIM_NONE;
    stripOff();
    flashPin = -1;
    resetGestureState();
    handInZone = false;
    outOfRangeSince = 0;
    zone1Max = DEFAULT_ZONE1_MAX;
    zone2Max = DEFAULT_ZONE2_MAX;
    calibRefDist = -1;
    calibStableSince = 0;
    calibNoObjectSince = 0;
    isCalibrated = false;
    wasConnected = false;
    Serial.println("BLE disconnected");
  }

  float current = readDistanceCm();
  updateCalibration(current);

  if (millis() - lastDebugPrint >= DEBUG_PRINT_INTERVAL) {
    lastDebugPrint = millis();
    Serial.print("dist=");
    if (current < 0) Serial.print("--");
    else Serial.print(current);
    Serial.print("cm  zone1=0-");
    Serial.print(zone1Max);
    Serial.print(" zone2=");
    Serial.print(zone1Max);
    Serial.print("-");
    Serial.print(zone2Max);
    Serial.print(isCalibrated ? "  [calibrated]" : "  [default]");
    Serial.print(bleConnected ? "" : "  (not connected)");
    Serial.println();
  }

  if (!bleConnected) return;

  // The Python app used to run this ramp and stop it with "VS"; the board owns
  // it now, so it also owns deciding when to stop.
  if (volumeActive && millis() - lastVolumeTick >= VOLUME_INTERVAL) {
    lastVolumeTick = millis();
    sendMediaKey(volumeUp ? KEY_VOL_UP : KEY_VOL_DOWN, volumeUp ? "VOL+" : "VOL-");
    if (++volumeSteps >= MAX_VOLUME_STEPS) volumeActive = false;
  }

  if (!volumeActive && (anim == ANIM_VOL_UP || anim == ANIM_VOL_DN)) {
    anim = ANIM_NONE;
    stripOff();
  }

  // A gesture animation always wins for its ~360ms; the bar resumes underneath.
  if (millis() - lastFrame >= FRAME_MS) {
    lastFrame = millis();
    if (anim != ANIM_NONE) renderAnim();
    else if (amsFound)     renderProgress();
  }

  if (authDone && !amsFound && amsTries < 5 && millis() >= amsNextTry) {
    amsTries++;
    amsNextTry = millis() + 2000;
    setupAMS();
  }

  handleFlash();

  bool inZone = (current >= DETECT_MIN && current <= zone2Max);

  if (inZone) outOfRangeSince = 0;
  else if (outOfRangeSince == 0) outOfRangeSince = millis();
  bool handConfirmedGone = (!inZone && outOfRangeSince != 0 &&
                            millis() - outOfRangeSince >= OUT_OF_RANGE_MS);

  if (inZone && !handInZone) {
    handInZone = true;
    handEntryTime = millis();
    holdFired = false;
    passMinDist = current;
  }

  else if (handConfirmedGone && handInZone) {
    handInZone = false;
    outOfRangeSince = 0;

    // Closest approach over the whole pass decides the zone. Taking it from the single
    // reading at entry meant one noisy sample near 15cm flipped a zone-1 wave into
    // zone 2, breaking the double-pass match so PREV came out as NEXT.
    handZone = (passMinDist <= zone1Max) ? 1 : 2;
    passMinDist = 999;

    if (!holdFired && !volumeActive) {
      if (passCount == 0) {
        passCount = 1;
        passZone = handZone;
        firstPassExitTime = millis();
      } else if (passCount == 1 && handZone == passZone && millis() - firstPassExitTime <= DOUBLE_PASS_WINDOW) {
        if (passZone == 1) {
          sendMediaKey(KEY_PREV, "PREV");
          startAnim(ANIM_PREV);
        } else {
          volumeUp = false;
          volumeActive = true;
          volumeSteps = 0;
          lastVolumeTick = 0;
          startAnim(ANIM_VOL_DN);
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
    if (current < passMinDist) passMinDist = current;

    if (!holdFired && millis() - handEntryTime >= HOLD_TIME) {
      // The app used to make this call: a hold during a volume ramp stops the
      // ramp rather than toggling playback.
      if (volumeActive) {
        volumeActive = false;
      } else {
        sendMediaKey(KEY_PLAY_PAUSE, "PLAY/PAUSE");
        startAnim(ANIM_PULSE);
      }
      flashLed(ledPause);
      holdFired = true;
      resetGestureState();
    }
  }

  if (!handInZone && passCount == 1 && !volumeActive && millis() - firstPassExitTime > DOUBLE_PASS_WINDOW) {
    if (passZone == 1) {
      sendMediaKey(KEY_NEXT, "NEXT");
      startAnim(ANIM_NEXT);
    } else {
      volumeUp = true;
      volumeActive = true;
      volumeSteps = 0;
      lastVolumeTick = 0;
      startAnim(ANIM_VOL_UP);
    }
    passCount = 0;
  }
}
