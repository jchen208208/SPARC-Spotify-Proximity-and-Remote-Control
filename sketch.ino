#include <HCSR04.h>


//define the pins for the sensor
byte triggerPin = 13;
byte echoPin = 12;


//define the pins for the two modes
int ledSkip = 2;
int ledVolume = 3;

//define pin for pause
int ledPause = 4;

//define pins for Up/down
int ledUp = 5;
int ledDown = 6;

//make the counter and confirmation delay to swtich mode
int CntTimer = 0;
int CntSConfirm = 40;

//stuff for making sure the previous value is stored
const int NUM_READINGS = 3;
double history[NUM_READINGS];
int idx = 0;


//tiemr stuff
unsigned long handEnteredTime = 0;
unsigned long handEnteredTimeP = 0;
bool handInRange = false;
int currentMode = 0; // 0 = volume, 1 = skip, 2 = pause/play


//constants for the zones
const int NEAR_ZONE = 15;   // 2-15cm = skip mode
const int FAR_ZONE = 30;    // 15-30cm = volume mode
const int HOLD_TIME = 2000; // 3 seconds in ms
const int COOLDOWN_MS = 1000;
unsigned long lastModeSwitch = 0;

void setup () {
  Serial.begin(9600);
  //
  HCSR04.begin(triggerPin, echoPin);
  
  
  pinMode(ledSkip, OUTPUT);
  pinMode(ledVolume, OUTPUT);
  pinMode(ledUp, OUTPUT);
  pinMode(ledDown, OUTPUT);
  pinMode(ledPause, OUTPUT);

  for (int i = 0; i < NUM_READINGS; i++) {
    history[i] = 0.0;
  }
}

void loop () {
  
  
  //store the distance into a pointer
  double* distances = HCSR04.measureDistanceCm();
  double current = distances[0];

  // get previous reading before we overwrite it
  double previous = history[(idx - 1 + NUM_READINGS) % NUM_READINGS];
  
 
  // store current reading into history
  history[idx] = current;
  idx = (idx + 1) % NUM_READINGS;

  //see if its in range
  bool inRange = (current >= 2 && current <= 30);
  
  double change = current - previous;

  if (inRange && !handInRange) {
    // hand just entered range
    handInRange = true;
    handEnteredTime = millis();
    handEnteredTimeP = millis();
  } 
  
  else if (!inRange && handInRange) {
    // hand just left range
    handInRange = false;
    unsigned long holdDurationOUT = millis() - handEnteredTimeP;

    if (holdDurationOUT < HOLD_TIME) {
      // removed before 3 seconds = pause/play
      Serial.println("P");
      digitalWrite(ledPause, HIGH);
      //Serial.print(holdDurationOUT);
    }

    // if they held for 3+ seconds, mode was already set when timer expired

  } 
  
  else if (inRange && handInRange) {
    // hand is still in range, check if 3 seconds have passed
    unsigned long holdDurationIN = millis() - handEnteredTime;

    if (holdDurationIN >= HOLD_TIME && millis() - lastModeSwitch > COOLDOWN_MS) {
      // 3 seconds reached, check which zone
      if (current < NEAR_ZONE) {
        //set mode
        currentMode = 1;

        //config led's
        digitalWrite(ledSkip, HIGH);
        digitalWrite(ledVolume, LOW);

        //Serial.println("MODE: Skip/Previous");
        //Serial.print(holdDurationIN);

        //detect changes
        if (change > 1.0) {
            Serial.println("S+");
            digitalWrite(ledUp, HIGH);
            digitalWrite(ledDown, LOW);
          } else if (change < -1.0) {
            Serial.println("S-");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, HIGH);
          } else {
            //Serial.println("S=");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, LOW);
          }

      } 
      else {
        //set mode
        currentMode = 0;

        //config led's
        digitalWrite(ledVolume, HIGH);
        digitalWrite(ledSkip, LOW);


        //Serial.println("MODE: Volume");
        //Serial.print(holdDurationIN);

        //detect changes
        if (change > 1.0) {
            Serial.println("V+");
            digitalWrite(ledUp, HIGH);
            digitalWrite(ledDown, LOW);
          } else if (change < -1.0) {
            Serial.println("V-");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, HIGH);
          } else {
            //Serial.println("V=");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, LOW);
          }

      }
      lastModeSwitch = millis();
      handEnteredTime = millis(); // reset so it doesnt keep triggering
    }
  }

  else {
      //clearing the led's
      digitalWrite(ledSkip, LOW);
      digitalWrite(ledVolume, LOW);
      digitalWrite(ledPause, LOW);
    }
    delay(100);
  }
