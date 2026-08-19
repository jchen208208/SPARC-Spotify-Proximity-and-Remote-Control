// SPARC as a BLE HID media remote - no Spotify API, no companion app.
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>

#include <Wire.h>
#include "Adafruit_VL53L0X.h"
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

#include <FastLED.h>
#include <Preferences.h>
Preferences prefs;

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
volatile bool bleConnected = false;
bool wasConnected = false;

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

bool handInZone = false;
int handZone = 0;
unsigned long handEntryTime = 0;
bool holdFired = false;

int passCount = 0;
int passZone = 0;
unsigned long firstPassExitTime = 0;

// When volume is actively ramping, block all pass gestures
bool volumeActive = false;
bool volumeUp = false;
unsigned long lastVolumeTick = 0;
const unsigned long VOLUME_INTERVAL = 200;

// Nothing comes back over HID, so the host's real volume is unknowable and the bar
// is an estimate: macOS and iOS move 1/16 of the range per press, Windows 2%. It
// self-corrects at the rails - ramping to 0 or 100 puts the real volume there too.
// Persisted to NVS so a power cycle doesn't drop it back to a wrong half-full bar.
int volEstimate = 50;
const int VOLUME_STEP = 6;
bool volDirty = false;

// Whether volEstimate means anything yet for the host we're currently talking to.
bool calibrated = false;
char peerAddr[24] = "";

int outOfRangeCount = 0;
unsigned long lastReadTime = 0;

// False if the VL53L0X never came up. Gestures stop working, but Bluetooth keeps
// running so the board still pairs and looks alive, rather than going dark.
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

// pos 0..1 along the strip. SPARC blue: light blue at the start → dark blue at the end.
CRGB barColour(float pos) {
  int r = map(pos * 100, 0, 100, 120,   0);
  int g = map(pos * 100, 0, 100, 200,   0);
  int b = map(pos * 100, 0, 100, 255, 120);
  return CRGB(r, g, b);
}

void updateVolumeLEDs(int vol) {
  int bars = map(vol, 0, 100, 0, NUMPIXELS);
  if (vol > 0 && bars == 0) bars = 1; // any nonzero volume shows at least 1 LED

  for (int i = 0; i < NUMPIXELS; i++) {
    leds[i] = (i < bars) ? barColour((float)i / (NUMPIXELS - 1)) : CRGB::Black;
  }
  FastLED.show();
}

// Whole strip in the gradient's middle colour: connected, but this host's volume is
// still unknown. Clears as soon as a volume gesture gives us something to track.
void showUncalibrated() {
  for (int i = 0; i < NUMPIXELS; i++) leds[i] = barColour(0.5);
  FastLED.show();
}

void showBar() {
  if (!bleConnected)    updateVolumeLEDs(0);  // dark when nothing is paired
  else if (!calibrated) showUncalibrated();
  else                  updateVolumeLEDs(volEstimate);
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

// Only flags state - the LED teardown happens in loop() so FastLED is never
// driven from the BLE task.
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *server, NimBLEConnInfo &info) override {
    strncpy(peerAddr, info.getAddress().toString().c_str(), sizeof(peerAddr) - 1);
    peerAddr[sizeof(peerAddr) - 1] = '\0';
    bleConnected = true;
  }
  void onDisconnect(NimBLEServer *server, NimBLEConnInfo &info, int reason) override {
    bleConnected = false;
    NimBLEDevice::startAdvertising(); // or it never comes back
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

  NimBLEServer *server = NimBLEDevice::createServer();
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

  prefs.begin("sparc", false);

  // On a cold power-up the VL53L0X shares the ESP32's rail and is still booting
  // when we get here, so an immediate begin() finds nothing. Wait, retry, and
  // carry on either way so a dead sensor can't take Bluetooth down with it.
  delay(200);
  for (int i = 0; i < 5 && !sensorReady; i++) {
    sensorReady = lox.begin();
    if (!sensorReady) delay(200);
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
    // A different host's volume has nothing to do with the last one's, so only a
    // returning host gets its tracked level back.
    if (prefs.getString("peer", "") == peerAddr) {
      volEstimate = prefs.getInt("vol", 50);
      calibrated  = prefs.getBool("cal", false);
    } else {
      calibrated = false;
      prefs.putString("peer", peerAddr);
      prefs.putBool("cal", false);
    }
    showBar();
    Serial.print("BLE connected: ");
    Serial.println(peerAddr);
  } else if (!bleConnected && wasConnected) {
    digitalWrite(ledPause, LOW);
    updateVolumeLEDs(0);
    flashPin = -1;
    resetGestureState();
    handInZone = false;
    outOfRangeCount = 0;
    wasConnected = false;
    Serial.println("BLE disconnected");
  }

  if (!bleConnected) return;

  // The Python app used to run this ramp and stop it with "VS"; the board owns
  // it now, so it also owns deciding when to stop.
  if (volumeActive && millis() - lastVolumeTick >= VOLUME_INTERVAL) {
    lastVolumeTick = millis();
    if (volumeUp) {
      sendMediaKey(KEY_VOL_UP, "VOL+");
      volEstimate = constrain(volEstimate + VOLUME_STEP, 0, 100);
      if (volEstimate >= 100) volumeActive = false;
    } else {
      sendMediaKey(KEY_VOL_DOWN, "VOL-");
      volEstimate = constrain(volEstimate - VOLUME_STEP, 0, 100);
      if (volEstimate <= 0) volumeActive = false;
    }
    calibrated = true;
    updateVolumeLEDs(volEstimate);
    volDirty = true;
  }

  // NVS wears out with writes, so save once the ramp settles rather than per tick.
  if (volDirty && !volumeActive) {
    prefs.putInt("vol", volEstimate);
    prefs.putBool("cal", true);
    volDirty = false;
  }

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
          sendMediaKey(KEY_PREV, "PREV");
        } else {
          volumeUp = false;
          volumeActive = true;
          lastVolumeTick = 0;
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
      // The app used to make this call: a hold during a volume ramp stops the
      // ramp rather than toggling playback.
      if (volumeActive) {
        volumeActive = false;
      } else {
        sendMediaKey(KEY_PLAY_PAUSE, "PLAY/PAUSE");
      }
      flashLed(ledPause);
      holdFired = true;
      resetGestureState();
    }
  }

  if (!handInZone && passCount == 1 && !volumeActive && millis() - firstPassExitTime > DOUBLE_PASS_WINDOW) {
    if (passZone == 1) {
      sendMediaKey(KEY_NEXT, "NEXT");
    } else {
      volumeUp = true;
      volumeActive = true;
      lastVolumeTick = 0;
    }
    passCount = 0;
  }
}
