// uses BLE HID media remote with no Spotify API or app.
// BLE (BLuetooth Low Energy) transmits and recieves bytes with the device
// HID (Human Interface Device) defines what those bytes mean (i.e. ours functions as a media input device)
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>

#include <Wire.h>
#include "Adafruit_VL53L0X.h"
Adafruit_VL53L0X sensor = Adafruit_VL53L0X();  // creates the sensor object

#include <FastLED.h>

// defines which variables we use
#define BOARD 2  // 2 = PCB v2, 1 = PCB v1, 0 = perfboard

// preprocessor directive
#if BOARD == 2
  #define LED_PIN 15
  #define I2C_SDA 16
  #define I2C_SCL 17
  const int ledPause = 27; // status LED
#elif BOARD == 1
  #define LED_PIN 15
  #define I2C_SDA 17
  #define I2C_SCL 16
  const int ledPause = 27;
#else
  #define LED_PIN 18
  #define I2C_SDA 21
  #define I2C_SCL 22
  const int ledPause = 4;
#endif

#define NUMPIXELS 8  // our strip has 8 LED's
CRGB leds[NUMPIXELS];  // CRGB type holds three numbers for each entry (rgb value for each LED)

// declaring the 7 functions so an input signal can be sent as one byte over BLE to the device which each bit representing a button
static const uint8_t REPORT_ID = 1;
static uint8_t reportMap[] = {
  0x05, 0x0C,  // Usage Page (Consumer)
  0x09, 0x01,  // Usage (Consumer Control)
  0xA1, 0x01,  // Collection (Application)
  0x85, REPORT_ID,  //   Report ID (1)
  0x15, 0x00,  //   Logical Minimum (0), meaning each "button" is on or off
  0x25, 0x01,  //   Logical Maximum (1)
  0x75, 0x01,  //   Report Size (1), each input takes 1 bit
  0x95, 0x07,  //   Report Count (7), and there are 7 inputs
  0x09, 0xB5,  //   Scan Next Track, bit 0
  0x09, 0xB6,  //   Scan Previous Track, bit 1
  0x09, 0xB7,  //   Stop, ...
  0x09, 0xCD,  //   Play/Pause
  0x09, 0xE2,  //   Mute
  0x09, 0xE9,  //   Volume Increment
  0x09, 0xEA,  //   Volume Decrement, bit 6
  0x81, 0x02,  //   Input (Data, Variable, Absolute)
  0x95, 0x01,  //   Report Count (1)
  0x81, 0x03,  //   Input (Constant)
  0xC0  // End Collection
};

const uint8_t KEY_NEXT = 1 << 0;  // 00000001
const uint8_t KEY_PREV = 1 << 1;  // 00000010
const uint8_t KEY_PLAY_PAUSE = 1 << 3;  // 00001000
const uint8_t KEY_VOL_UP = 1 << 5;  // 00100000
const uint8_t KEY_VOL_DOWN = 1 << 6;  // 01000000

// A zero-length press is ignored by some devices so we need to hold the bit briefly.
const unsigned long KEY_HOLD_MS = 15;

NimBLEHIDDevice *hid = nullptr;
NimBLECharacteristic *inputReport = nullptr;  // the channel that transports the input byte
NimBLEServer *pServer = nullptr;
volatile bool bleConnected = false;  // volatile means the variable can change without the code inside main() touching it
volatile bool authDone = false;
volatile uint16_t connHandle = 0;
bool wasConnected = false;  // if (bleConnected && !wasConnected) = just connected,  if (!bleConnected && wasConnected) = just disconnected
char peerAddr[24] = "";  // holds the device's BT address

// Apple Media Service: Apple's protocol that lets a BT device read what's currently playing on an iPhone.
// used to determine if the paired device is an iPhone?
static const char *AMS_SERVICE       = "89D3502B-0F36-433A-8EF4-C502AD55F8DC";
static const char *AMS_ENTITY_UPDATE = "2F7CABCE-808D-411F-9A0C-BB92BA96C102";

bool amsFound = false;
int amsTries = 0;  // gives up after 5 attempts
unsigned long amsNextTry = 0;  // when to retry

float amsElapsed = 0;  // seconds into the track, as of amsLastUpdate
float amsRate = 1.0;  // playback speed
float amsDuration = 0;  // track length
int amsState = 0;  // 0 paused, 1 playing, 2 rewinding, 3 fast-forwarding
unsigned long amsLastUpdate = 0;

// detection zone boundary in cm. whatever's parked within 1m of the sensor for 5+ seconds becomes the "back wall," and the gesture zone is from the sensor to the wall.
// falls back to a fixed 30cm boundary when nothing is sitting in range.
const float DETECT_MIN = 2.0;
const float DEFAULT_ZONE_MAX = 30.0;
float zoneMax = DEFAULT_ZONE_MAX;

const float CALIBRATION_RANGE = 100.0;  // only consider objects within 1m
const float CALIBRATION_MIN = 10.0;  // any stable object within 10cm is more likely to be a resting hand
const unsigned long CALIBRATION_HOLD_MS = 5000;  // must sit still 5 seconds to lock in calibration
const float CALIBRATION_TOLERANCE = 3.0;  // cm of wobble still counted as "the same object"
const unsigned long CALIB_LOSS_MS = 1000;  // how long to wait for
const float CALIBRATION_MARGIN = 0.2;

float calibRefDist = -1;  // the wall's distance
unsigned long calibStableSince = 0;  // when it stopped moving
unsigned long calibNoObjectSince = 0;  // when it disappeared
bool isCalibrated = false;

// gesture timings
const unsigned long HOLD_TIME = 300;
const unsigned long DOUBLE_PASS_WINDOW = 800;
const unsigned long OUT_OF_RANGE_MS = 100;

// Non-blocking LED flash
int flashPin = -1;
unsigned long flashStart = 0;
const unsigned long FLASH_DURATION = 150;

bool handInZone = false;
unsigned long handEntryTime = 0;
bool holdFired = false;

int passCount = 0;
unsigned long firstPassExitTime = 0;

// Volume mode: entered when a hold drifts instead of staying steady. while active, hand movement acts as a slider: <this many> cm of travel sends one volume step in that direction.
bool volumeActive = false;
float volumeRefDist = 0;  // distance we're measuring movement from
const float VOLUME_CM_PER_STEP = 0.5;  // 0.5 cm = 1 volum block
// How long to wait to enter volume mode
const unsigned long VOLUME_ENTER_MS = 600;

// used to detect volume mode entry
unsigned long stillSince = 0;
float prevDistance = 0;
const float MOVE_NOISE = 0.75;

// animation enums
enum Anim {ANIM_NONE, ANIM_NEXT, ANIM_PREV, ANIM_PULSE, ANIM_VOL_UP, ANIM_VOL_DN};
Anim anim = ANIM_NONE;
unsigned long animStart = 0;
const unsigned long ANIM_STEP = 45;  // how man ms an LED lights up for
const unsigned long PULSE_MS = 320;  // how long the play/pause animation lasts for
const unsigned long FRAME_MS = 20;  // the minimum gap between two strip animations
unsigned long lastFrame = 0;
unsigned long outOfRangeSince = 0;

// variables used for debugging
const unsigned long DEBUG_PRINT_INTERVAL = 200;
unsigned long lastDebugPrint = 0;

bool sensorReady = false;

// returns sensor distance
float readDistanceCm() {
  if (!sensorReady) {
    return -1;
  }
  VL53L0X_RangingMeasurementData_t reading;  // holds the distance, a status code, signal strength.
  sensor.rangingTest(&reading, false);  // false turns off the library's debug printing
  if (reading.RangeStatus != 0) {
    return -1;  // 0 is the only valid status, 1,2,3,5 return garbage
  }
  return reading.RangeMilliMeter / 10.0; // converts mm to cm
}

// calibration function, current is the latest distance reading
void updateCalibration(float current) {
  bool objectPresent = (current >= DETECT_MIN && current <= CALIBRATION_RANGE);  // true if something's between 2cm and 1m. 

  if (!objectPresent) {
    // nothing within 1m = debounce briefly then fall back to the fixed default zone.
    if (calibNoObjectSince == 0) {
      calibNoObjectSince = millis();
    }
    if (isCalibrated && (millis() - calibNoObjectSince >= CALIB_LOSS_MS)) {
      zoneMax = DEFAULT_ZONE_MAX;
      isCalibrated = false;
      Serial.println("No object within 1m - zone reset to default 30cm");
    }
    calibRefDist = -1;
    calibStableSince = 0;
    return;
  }

  // if it reaches here, that means object is within 1m
  calibNoObjectSince = 0;

  float diff = current - calibRefDist;
  if (diff < 0) {
    diff = -diff;
  }

  if (calibRefDist < 0 || diff > CALIBRATION_TOLERANCE) {
    // first sighting or the reading moved enough that this isn't the same object
    calibRefDist = current;
    calibStableSince = millis();
    return;
  }

  // if it reaches here, that means there's been a stable object for some time
  calibRefDist = (calibRefDist * 0.9f) + (current * 0.1f); // smooth out sensor jitter

  if (millis() - calibStableSince >= CALIBRATION_HOLD_MS && calibRefDist >= CALIBRATION_MIN) {
    // the object held still 5s and it's at least 10cm away
    float margin = calibRefDist * CALIBRATION_MARGIN; // 20% of the distance
    if (margin < CALIBRATION_TOLERANCE) {
      margin = CALIBRATION_TOLERANCE;  // never under 3cm
    }
    zoneMax = calibRefDist - margin;  // stops the zone just short of the wall bc if you parked zoneMax on the wall, sensor jitter would make the wall itself flicker in and out of the zone 
    if (!isCalibrated) {
      isCalibrated = true;
      Serial.print("Zones calibrated to object at ");
      Serial.print(calibRefDist);
      Serial.println("cm");
    }
  }
}

// LED helpers
void flashLed(int pin) {
  if (flashPin >= 0) {
    digitalWrite(flashPin, LOW);  // stop any flash already running
  }
  digitalWrite(pin, HIGH);
  flashPin = pin;
  flashStart = millis();
}

void handleFlash() {
  if (flashPin >= 0 && millis() - flashStart >= FLASH_DURATION) {
    digitalWrite(flashPin, LOW);  // stops any flash over 150ms
    flashPin = -1;
  }
}

// pos = 0.0 to 1.0 along the strip. the strip fades light blue (120, 200, 255) at the start tp dark blue (0, 0, 120) at the end
CRGB barColour(float pos) {
  int r = map(pos * 100, 0, 100, 120, 0);  // "pos*100 is somewhere in 0–100. Map it into 120–0."
  int g = map(pos * 100, 0, 100, 200, 0);
  int b = map(pos * 100, 0, 100, 255, 120);
  return CRGB(r, g, b);
}

void stripOff() {
  FastLED.clear();
  FastLED.show();
}

// AMS only pushes on change, so interpolate between updates for a smooth bar.
void renderProgress() {
  if (amsDuration < 1.0) {
    return; // no track loaded yet
  }

  float pos = amsElapsed;
  if (amsState == 1) {  // if track playing
    pos += (millis() - amsLastUpdate) / 1000.0f * amsRate;  // updates how far along the track (ms to s)
  }
  float frac = constrain(pos / amsDuration, 0.0f, 1.0f);  // fraction of track done

  float lit = frac * NUMPIXELS;  // amoutn of LEDs that should be lit
  int full = (int)lit;  // how many fully lit ones
  uint8_t partial = (uint8_t)((lit - full) * 255);  // partial brightness: 0.52 = 133 brightness or 133 flashes out of a 255 cycle

  for (int i = 0; i < NUMPIXELS; i++) {
    leds[i] = barColour((float)i / (NUMPIXELS - 1));  // stores the leds' colour
    if (i > full) {
      leds[i] = CRGB::Black;  // if it's past the progress bar, turn it black (no colour)
    }
    else if (i == full) {
      leds[i].nscale8(partial); // the last led is dimmed
    }
  }
  FastLED.show();
}

// starts a gesture animation
void startAnim(Anim a) {
  anim = a;
  animStart = millis();
}

// draws one animation frame and clears itself when done.
void renderAnim() {
  if (anim == ANIM_NONE) {
    return;
  }

  unsigned long t = millis() - animStart;  // ms into the animation

  // pause/play animation
  if (anim == ANIM_PULSE) {
    if (t >= PULSE_MS) {
      anim = ANIM_NONE;
      stripOff();
      return;
      }
    uint8_t fade = 255 - (255 * ((float)t / PULSE_MS));  // the longer into the pulse animation, the dimmer the leds.
    for (int i = 0; i < NUMPIXELS; i++) {
      leds[i] = barColour((float)i / (NUMPIXELS - 1));
      leds[i].nscale8(fade);
    }
    FastLED.show();
    return;
  }

  // the swipes (next/prev/volume)
  unsigned long frame = t / ANIM_STEP;  // 45ms per step so ex: 90ms = frame 2
  bool looping = (anim == ANIM_VOL_UP || anim == ANIM_VOL_DN);
  if (!looping && frame >= NUMPIXELS) {
    // next/prev swipe once and stop (so 8 frames × 45ms = 360ms). 
    anim = ANIM_NONE;
    stripOff();
    return;
    }

  int head = frame % NUMPIXELS;
  if (anim == ANIM_PREV || anim == ANIM_VOL_DN) {
    head = NUMPIXELS - 1 - head;  // mirrors the position
  }

  for (int i = 0; i < NUMPIXELS; i++) {
    int behind = (anim == ANIM_PREV || anim == ANIM_VOL_DN) ? i - head : head - i;  //  how far this current LED sits behind the moving head.
    if (behind >= 0 && behind < 3) {  // if the led is "behind" the head. behind < 3 is so that our swipe animation only has a trail of 3 LEDs
      leds[i] = barColour((float)i / (NUMPIXELS - 1));
      leds[i].nscale8(255 >> (behind * 2));  // >> shifts the binary digits to the right. Each shift drops the rightmost bit which halves the number.
      // (behind * 2) causes an eponentially dimmer tail: (255, 63, 15)
    }
    else {
      leds[i] = CRGB::Black;
    }
  }
  FastLED.show();
}

// press a "button" and release. Leaving a bit set = a stuck key
void sendMediaKey(uint8_t byte, const char *label) {
  if (!bleConnected || inputReport == nullptr) {  // if no device connected or the input channel doesn't exist yet
    return;
  }

  uint8_t value = byte;
  inputReport->setValue(&value, 1);  // loads one byte intot he channel
  (*inputReport).notify();  // notify() sends one byte to the phone
  delay(KEY_HOLD_MS);  // 15ms, wait then release
  value = 0;
  inputReport->setValue(&value, 1);  // all 0's = nothing is pressed, aka release
  inputReport->notify();

  Serial.print("SENT: ");
  Serial.println(label);
}

// called whenever iOS pushes an update
// notification payload: [EntityID][AttributeID][flags][value as UTF-8].
void amsNotify(NimBLERemoteCharacteristic *chr, uint8_t *data, size_t len, bool isNotify) {
  if (len < 3) {
    return;  // too short to even have header bytes
  }

  char buf[48];
  size_t n = len - 3;  // how many text bytes there are
  if (n >= sizeof(buf)) {
    n = sizeof(buf) - 1;  // prevents buffer overflow
  }
  memcpy(buf, data + 3, n);  // data + 0 and 1 are headers, data + 2 is flags, and data + 3 is the actualy text data
  buf[n] = '\0';

  if (data[0] == 0 && data[1] == 1) {  // Player, PlaybackInfo
    // "state,rate,elapsed": i.e. "1,1.0,45.2"
    char *fields[3] = {buf, nullptr, nullptr};
    int nfields = 1;
    for (char *c = buf; *c && nfields < 3; c++) {
      if (*c == ',') {
        *c = '\0';
        fields[nfields++] = c + 1;  // stores the address of the character after the comma
        }
    }
    if (amsState == 1) {  // prevents lag behinds on the progress bar if given a partial PlaybackInfo update
      amsElapsed += (millis() - amsLastUpdate) / 1000.0f * amsRate;
      amsLastUpdate = millis();
    }
    if (fields[0] && *fields[0]) {
      amsState = atoi(fields[0]); // string to int
    }
    if (fields[1] && *fields[1]) {
      amsRate = atof(fields[1]);  // string to float
    }
    if (fields[2] && *fields[2]) {
      amsElapsed = atof(fields[2]);  // if track is playing and there is an elapsed field, then the amsElpased we set just gets overwritten
    }
  }
  else if (data[0] == 2 && data[1] == 3) { // Track, Duration
    amsDuration = atof(buf);
  }
}

// connection attempt
void setupAMS() {
  NimBLEClient *client = pServer->getClient(connHandle);
  if (!client) {
    return;
  }

  NimBLERemoteService *service = client->getService(AMS_SERVICE);
  if (!service) { // not found means not iOS,
    Serial.println("AMS not offered - gesture animations only");
    return;
  }

  NimBLERemoteCharacteristic *eu = service->getCharacteristic(AMS_ENTITY_UPDATE);
  if (!eu || !eu->subscribe(true, amsNotify)) {  // hands over amsNotify as the callback, subscribe means whenever the value inside eu changes, run amsNotify
    Serial.println("AMS entity update subscribe failed");
    return;
  }

  const uint8_t wantPlayer[] = {0, 1}; // Player, PlaybackInfo: (state, rate, elapsed)
  const uint8_t wantTrack[]  = {2, 3}; // Track, Duration
  eu->writeValue(wantPlayer, sizeof(wantPlayer), true);
  eu->writeValue(wantTrack, sizeof(wantTrack), true);

  amsFound = true;  // only set if everything previously succeeded
  Serial.println("AMS connected - progress bar active");
}

// required for NimBLE connection events
class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *server, NimBLEConnInfo &info) override {
    // ovverid deliberately replaces a base-class fucntion
    strncpy(peerAddr, info.getIdAddress().toString().c_str(), sizeof(peerAddr) - 1);  // .c_str() creates the plain char pointer strncpy needs
    peerAddr[sizeof(peerAddr) - 1] = '\0';
    connHandle = info.getConnHandle();  // store which connection this is (needed later by setupAMS)
    bleConnected = true;
  }

  void onDisconnect(NimBLEServer *server, NimBLEConnInfo &info, int reason) override {
    bleConnected = false;
    authDone = false;
    amsFound = false;
    amsTries = 0;
    amsDuration = 0;
    NimBLEDevice::startAdvertising(); // a BLE device stopes advertising the moment it connects so we want to make sure it starts advertising again on disconnect
  }

  // fires once bonding is done
  void onAuthenticationComplete(NimBLEConnInfo &info) override {
    authDone = true;
    amsNextTry = 0;
  }
};

// called on disconnect or when a hold fires
void resetGestureState() {
  passCount = 0;
  volumeActive = false;
}


void setup() {
  Serial.begin(9600);
  Serial.println("SPARC booting (BLE HID)");

  NimBLEDevice::init("SPARC");  // name you see when pairing
  NimBLEDevice::setSecurityAuth(true, false, true);  // bonding (pairing is remembered across reboots), no MITM, secure connections
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);  // no input or output (no passcode on connection)


  // the server holds our services and owns the connections. 
  pServer = NimBLEDevice::createServer();  // empty server
  NimBLEServer *server = pServer;
  server->setCallbacks(new ServerCallbacks());

  hid = new NimBLEHIDDevice(server);  // tells teh HID object where to attach
  inputReport = hid->getInputReport(REPORT_ID);
  hid->setManufacturer("SPARC");
  hid->setPnp(0x02, 0x303A, 0x0001, 0x0100);
  hid->setHidInfo(0x00, 0x01);
  hid->setReportMap(reportMap, sizeof(reportMap));  // sets the HID service (the 7-button actions)
  hid->setBatteryLevel(100);  // The board never measures its actual battery for now, so hardcode at 100%
  server->start();

  // advertising: GENERIC_HID, not HID_KEYBOARD
  NimBLEAdvertising *adv = NimBLEDevice::getAdvertising();
  adv->setAppearance(GENERIC_HID);
  adv->addServiceUUID(hid->getHidService()->getUUID());
  adv->enableScanResponse(true);
  adv->start();

  Wire.begin(I2C_SDA, I2C_SCL);  // starts the I2C as master on these two pings
  FastLED.addLeds<WS2812B, LED_PIN, GRB>(leds, NUMPIXELS);  // registers the our led strip
  FastLED.setBrightness(10);  // brightness 10 out of 255. Affects every animation and progress bar. ex: head of the swipe animation: colour 120,200,255  ×  255/255 (nscale8)  ×  10/255 (brightness)  =  5,8,10. nscale() is applied to one pixel while setBrightness is applied to every pixel.
  FastLED.clear();
  FastLED.show();


  // On power-up, the VL53L0X shares the ESP32's rail and is still booting when we get here, so an immediate begin() finds nothing. Wait, retry up to 5 times, and carry on either way so a dead sensor doesn't take Bluetooth down with it.
  delay(200);
  for (int i = 0; i < 5 && !sensorReady; i++) {
    sensorReady = sensor.begin();
    if (!sensorReady) {
      delay(200);
    }
  }

  if (sensorReady) {
    sensor.configSensor(Adafruit_VL53L0X::VL53L0X_SENSE_LONG_RANGE);  // long-range mode to look more than 1m for calibration,
    sensor.setMeasurementTimingBudgetMicroSeconds(20000);  // how long the sensor spends on one measurement
  }

  if (sensorReady) {
    Serial.println("VL53L0X ready");
  }
  else {
    Serial.printf("VL53L0X NOT FOUND - check wiring (SDA=%d, SCL=%d, 3V3, GND)\n", I2C_SDA, I2C_SCL);
  }

  pinMode(ledPause, OUTPUT);

  Serial.println("setup done - advertising as SPARC");
}


void loop() {
  if (bleConnected && !wasConnected) {  // just connected
    wasConnected = true;
    Serial.print("BLE connected: ");
    Serial.println(peerAddr);
  } else if (!bleConnected && wasConnected) {  // just disconnected
    digitalWrite(ledPause, LOW);
    anim = ANIM_NONE;
    stripOff();
    flashPin = -1;
    resetGestureState();
    handInZone = false;
    outOfRangeSince = 0;
    zoneMax = DEFAULT_ZONE_MAX;
    calibRefDist = -1;
    calibStableSince = 0;
    calibNoObjectSince = 0;
    isCalibrated = false;
    wasConnected = false;
    Serial.println("BLE disconnected");
  }

  float current = readDistanceCm();  // one sensor reading
  updateCalibration(current);

  if (millis() - lastDebugPrint >= DEBUG_PRINT_INTERVAL) {
    lastDebugPrint = millis();
    Serial.print("dist=");
    if (current < 0) {
      Serial.print("--");
    }
    else Serial.print(current);
    Serial.print("cm, zone=0-");
    Serial.print(zoneMax);
    Serial.print(isCalibrated ? ", [calibrated]" : ",, [default]");
    Serial.print(bleConnected ? "" : ", (not connected)");
    Serial.println();
  }

  if (!bleConnected) {
    return;
  }

  // renderAnim() never ends volume animations so loop() has to end it
  if (!volumeActive && (anim == ANIM_VOL_UP || anim == ANIM_VOL_DN)) {
    anim = ANIM_NONE;
    stripOff();
  }

  // frame rendering, animation has priority over progress bar
  if (millis() - lastFrame >= FRAME_MS) {
    lastFrame = millis();
    if (anim != ANIM_NONE) {
      renderAnim();
    }
    else if (amsFound) {
      renderProgress();
    }
  }

  // AMS retry block
  if (authDone && !amsFound && amsTries < 5 && millis() >= amsNextTry) {
    amsTries++;
    amsNextTry = millis() + 2000;
    setupAMS();  // try again
  }

  handleFlash();  // checks if the pause led has been on for more than 150ms

  bool inZone = (current >= DETECT_MIN && current <= zoneMax);

  if (inZone) {
    outOfRangeSince = 0;
  }
  else if (outOfRangeSince == 0) {  // if just out of range, start the timer
    outOfRangeSince = millis();
  }
  bool handConfirmedGone = (!inZone && outOfRangeSince != 0 && millis() - outOfRangeSince >= OUT_OF_RANGE_MS);

  if (inZone && !handInZone) {  // hand just in zone
    handInZone = true;
    handEntryTime = millis();
    holdFired = false;
    stillSince = millis();
    prevDistance = current;
  }

  else if (handConfirmedGone && handInZone) {  // hand just left
    handInZone = false;
    outOfRangeSince = 0;
    volumeActive = false;

    // distinguishing between a play/pause and a next/previous
    if (!holdFired) {  // only counts as a pass if it left before 300ms
      if (passCount == 0) {
        passCount = 1;
        firstPassExitTime = millis();
      }
      else if (passCount == 1 && millis() - firstPassExitTime <= DOUBLE_PASS_WINDOW) {
        sendMediaKey(KEY_PREV, "PREV");
        startAnim(ANIM_PREV);
        passCount = 0;
      }
      else {
        passCount = 1;
        firstPassExitTime = millis();
      }
    }
  }

  else if (inZone && handInZone) {
    if (!holdFired) {
      // if there's movement, update stillSince
      if (current - prevDistance > MOVE_NOISE || prevDistance - current > MOVE_NOISE) {
        stillSince = millis();
        prevDistance = current;
      }

      // if it's been 300ms since the last movement
      if (millis() - stillSince >= HOLD_TIME) {  // steady hold = play/pause. if hand is held still, play/pause will always fire before volume mode is entered
        holdFired = true;
        resetGestureState();
        sendMediaKey(KEY_PLAY_PAUSE, "PLAY/PAUSE");
        startAnim(ANIM_PULSE);
        flashLed(ledPause);
      }
      // if it's been 600ms and nothing's been held still for 300ms at a time
      else if (millis() - handEntryTime >= VOLUME_ENTER_MS) {  // unsteady hold = volume mode
        holdFired = true;
        resetGestureState();
        volumeActive = true;
        volumeRefDist = current;
      }
    }
    // volume block
    else if (volumeActive) {
      float delta = current - volumeRefDist;
      while (delta >= VOLUME_CM_PER_STEP) {
        sendMediaKey(KEY_VOL_UP, "VOL+");
        startAnim(ANIM_VOL_UP);
        volumeRefDist += VOLUME_CM_PER_STEP;
        delta -= VOLUME_CM_PER_STEP;
      }
      while (delta <= -VOLUME_CM_PER_STEP) {
        sendMediaKey(KEY_VOL_DOWN, "VOL-");
        startAnim(ANIM_VOL_DN);
        volumeRefDist -= VOLUME_CM_PER_STEP;
        delta += VOLUME_CM_PER_STEP;
      }
    }
  }

  if (!handInZone && passCount == 1 && millis() - firstPassExitTime > DOUBLE_PASS_WINDOW) {
    sendMediaKey(KEY_NEXT, "NEXT");
    startAnim(ANIM_NEXT);
    passCount = 0;
  }
}
