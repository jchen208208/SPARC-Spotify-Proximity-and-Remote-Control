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


//timer for the mode switch cooldown
unsigned long lastModeSwitch = 0;
const int COOLDOWN_MS = 100;

//stuff for making sure the previous value is stored
const int NUM_READINGS = 3;
double history[NUM_READINGS];
int idx = 0;
bool bufferFull = false; 

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
  double previous2 = history[(idx - 2 + NUM_READINGS) % NUM_READINGS];
  // store current reading into history
  history[idx] = current;
  idx = (idx + 1) % NUM_READINGS;
  if (idx == 0) bufferFull = true;

  //timer
  unsigned long currentmillis = millis();

  
  double change = current - previous;
  double change2 = previous - previous2;

  //detect whether somethings in range
  if (current && previous && previous2 < 50){
    //skip
    digitalWrite(ledSkip, HIGH);
    digitalWrite(ledVolume, LOW);

    //Serial.print(distances[0]);
    
    //Serial.println("mode = skip");
    //cooldown
    if (currentmillis - lastModeSwitch >= COOLDOWN_MS) { 
        lastModeSwitch = millis();
      //detect change
     if (change > 2.0) {
            Serial.println("S+");
            digitalWrite(ledUp, HIGH);
            digitalWrite(ledDown, LOW);
          } else if (change < -2.0) {
            Serial.println("S-");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, HIGH);
          } else {
            //Serial.println("S=");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, LOW);
          }
    }
  }

  else if(current && previous && previous2 < 100){

    //volume
    digitalWrite(ledVolume, HIGH);
    digitalWrite(ledSkip, LOW);

    //Serial.print(distances[0]);
    
    //Serial.println("mode = volume");
    //cooldown
    if (currentmillis - lastModeSwitch >= COOLDOWN_MS) { 
        lastModeSwitch = millis(); 
      //detect change
      if (change > 2.0) {
            Serial.println("V+");
            digitalWrite(ledUp, HIGH);
            digitalWrite(ledDown, LOW);
          } else if (change < -2.0) {
            Serial.println("V-");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, HIGH);
          } else {
            //Serial.println("V=");
            digitalWrite(ledUp, LOW);
            digitalWrite(ledDown, LOW);
          }
    }
    

  }
  //pause
  else if (abs(change) > 200){
      Serial.println("P");
      digitalWrite(ledPause, HIGH);
  }

  else {
      //clearing the led's
      digitalWrite(ledSkip, LOW);
      digitalWrite(ledVolume, LOW);
      digitalWrite(ledPause, LOW);
    }
    delay(50);
  }
   
  
  

