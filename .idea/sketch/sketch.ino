#include <HCSR04.h>

//define the pins for the sensor
const byte triggerPin = 13;
const byte echoPin = 12;

//define the pins for the two modes
const int ledSkip = 2;
const int ledVolume = 3;

//define pin for pause
const int ledPause = 4;

//define pins for Up/down
const int ledUp = 5;
const int ledDown = 6;

//define threshold distance for detection of input gestures
const int Thresh = 10;

//stuff for making sure the previous value is stored
const int NUM_READINGS = 3;
double history[NUM_READINGS];
int idx = 0;


//timer stuff
unsigned long handEnteredTime = 0;
unsigned long handEnteredTimePause = 0;
bool handInRange = false;
int currentMode = 0; // 0 = volume, 1 = skip, 2 = pause/play

//refresh timer
unsigned long lastReadTimeRefresh = 0;
const int READ_INTERVAL = 50; // read every 50ms instead of blocking

//constants for the zones
const int NEAR_ZONE = 50;   // 2-15cm = skip mode
const int FAR_ZONE = 100;    // 15-30cm = volume mode
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
  
  //refrsesh
  if (millis() - lastReadTimeRefresh < READ_INTERVAL) return; // skip if not time yet
  lastReadTimeRefresh = millis();

  //store the distance into a pointer
  double* distances = HCSR04.measureDistanceCm();
  double current = distances[0];

  // get previous reading before we overwrite it
  double previous = history[(idx - 1 + NUM_READINGS) % NUM_READINGS];
  
 
  // store current reading into history
  history[idx] = current;
  idx = (idx + 1) % NUM_READINGS;

  //see if its in range
  bool inRange = (current >= 2 && current <= 100);
  
  double change = current - previous;

  if (inRange && !handInRange) {
    // hand just entered range
    handInRange = true;
    handEnteredTime = millis();
    handEnteredTimePause = millis();
  } 
  
  else if (!inRange && handInRange) {
    // hand just left range
    handInRange = false;
    unsigned long holdDurationOUT = millis() - handEnteredTimePause;

    
    digitalWrite(ledSkip, LOW);
    digitalWrite(ledVolume, LOW);
    digitalWrite(ledUp, LOW);
    digitalWrite(ledDown, LOW);
    //digitalWrite(ledPause, LOW);
      

    if (holdDurationOUT < HOLD_TIME) {
      // removed before 3 seconds = pause/play
      Serial.println("P");
      digitalWrite(ledPause, !digitalRead(ledPause));
      //Serial.print(holdDurationOUT);
    }

    // if they held for 3+ seconds, mode was already set when timer expired

  } 
  
  else if (inRange && handInRange) {
    // hand is still in range, check if 3 seconds have passed
    unsigned long holdDurationIN = millis() - handEnteredTime;
    //Serial.print(distances[0]);
    //Serial.println("cm");

    if (holdDurationIN >= HOLD_TIME && millis() - lastModeSwitch > COOLDOWN_MS) {
      // 3 seconds reached, check which zone
      if (current < NEAR_ZONE) {
        //set mode
        currentMode = 1;

        //config led's
        digitalWrite(ledSkip, HIGH);
        digitalWrite(ledVolume, LOW);
        
      } 
      else if (current >= NEAR_ZONE && current < FAR_ZONE) {
        //set mode
        currentMode = 0;

        //config led's
        digitalWrite(ledVolume, HIGH);
        digitalWrite(ledSkip, LOW);



      }
      
      lastModeSwitch = millis();
      handEnteredTime = millis(); // reset so it doesnt keep triggering
    }

    if (currentMode == 1) {
      if (change > Thresh) {
        Serial.println("S+");
        digitalWrite(ledUp, HIGH);
        digitalWrite(ledDown, LOW);
      } else if (change < -Thresh) {
        Serial.println("S-");
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, HIGH);
      } else {
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, LOW);
      }
    } 
    
    else if (currentMode == 0) {
      if (change > Thresh) {
        Serial.println("V+");
        digitalWrite(ledUp, HIGH);
        digitalWrite(ledDown, LOW);
      } else if (change < -Thresh) {
        Serial.println("V-");
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, HIGH);
      } else {
        digitalWrite(ledUp, LOW);
        digitalWrite(ledDown, LOW);
      }

  }

  }
}
   
  
  

