// Arduino Nano: heat bed bang-bang control + DS18B20 + MPU6050 jump sensing.
// Libraries: OneWire, DallasTemperature, LiquidCrystal_I2C (Frank de Brabander) (Library Manager).
//
// Wiring:
//   DS18B20 data -> D2 (4.7k pull-up to 5V)
//   MOSFET gate  -> D9 (logic-level N-MOSFET, 100-220R gate resistor, 10k gate pull-down)
//   MPU6050 SDA  -> A4, SCL -> A5, VCC -> 5V (or 3.3V per breakout), GND -> GND
//   LCD 16x2 I2C backpack: SDA -> A4, SCL -> A5 (shared with MPU), VCC -> 5V, GND -> GND
//
// Serial 115200:
//   PC -> Nano : "S<setpoint>\n"  e.g. "S37.5" ; "S0" = heater off
//                "T<seconds left>\n" for the LCD timer ; "T-1" = no run
//   Nano -> PC : "<tempC>,<heater 0/1>,<accelPeak g>\n" every 100 ms
//                accelPeak = max |a| - 1g seen since last line

#include <Wire.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <LiquidCrystal_I2C.h>

const int PIN_ONEWIRE = 2;
const int PIN_HEATER = 9;
const float HYSTERESIS = 0.3;      // C, tune for your bed's thermal lag
const float MAX_TEMP = 60.0;       // hard safety cutoff
const unsigned long PC_TIMEOUT_MS = 5000;  // heater off if PC goes silent
const int MPU = 0x68;
const int LCD_ADDR = 0x27;  // 0x3F on some backpacks; run an I2C scanner if blank
// Standalone test without PC/GUI: set to a temp (e.g. 37.0) to heat to it on its own,
// LCD row 2 then shows uptime + shake (g) so the MPU can be checked by tapping the bed.
// Keep 0 for real runs, so the heater turns off whenever the PC goes silent.
const float STANDALONE_SETPOINT = 0;

OneWire oneWire(PIN_ONEWIRE);
DallasTemperature ds(&oneWire);
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);

float setpoint = 0;
float tempC = DEVICE_DISCONNECTED_C;
bool heater = false;
float accelPeak = 0;
float lcdPeak = 0;  // biggest shake since last LCD refresh (standalone display)
long secsLeft = -1;
unsigned long lastConv = 0, lastReport = 0, lastCmd = 0, lastLcd = 0;
char buf[16];
byte bufLen = 0;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_HEATER, OUTPUT);
  digitalWrite(PIN_HEATER, LOW);

  ds.begin();
  ds.setResolution(11);              // 375 ms conversion, 0.125 C
  ds.setWaitForConversion(false);
  ds.requestTemperatures();
  lastConv = millis();

  Wire.begin();
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission();     // wake
  Wire.beginTransmission(MPU); Wire.write(0x1C); Wire.write(0x10); Wire.endTransmission();  // +-8g

  lcd.init();
  lcd.backlight();
}

float readAccelG() {
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return 1.0;  // sensor missing: report "at rest"
  Wire.requestFrom(MPU, 6);
  if (Wire.available() < 6) return 1.0;
  float a[3];
  for (int i = 0; i < 3; i++) {
    int16_t raw = (Wire.read() << 8) | Wire.read();
    a[i] = raw / 4096.0;  // LSB/g at +-8g
  }
  return sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2]);
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      buf[bufLen] = 0;
      if (bufLen && buf[0] == 'S') {
        setpoint = constrain(atof(buf + 1), 0, MAX_TEMP);
        lastCmd = millis();
      } else if (bufLen && buf[0] == 'T') {
        secsLeft = atol(buf + 1);
      }
      bufLen = 0;
    } else if (bufLen < sizeof(buf) - 1) {
      buf[bufLen++] = c;
    }
  }
}

// Row 0: "T:36.50C S:37.0*" (* = heater on). Row 1: run time left.
void updateLcd(bool sensorOk, bool pcAlive, bool standalone, float target) {
  char t[8], s[8], line[17];
  if (sensorOk) dtostrf(tempC, 5, 2, t); else strcpy(t, " ERR ");
  dtostrf(target, 4, 1, s);
  snprintf(line, sizeof line, "T:%sC S:%s%c", t, s, heater ? '*' : ' ');
  lcd.setCursor(0, 0); lcd.print(line);
  if (standalone) {
    unsigned long up = millis() / 1000;
    char g[8];
    dtostrf(lcdPeak, 4, 2, g);
    snprintf(line, sizeof line, "Up %02lu:%02lu  %sg  ", up / 60 % 100, up % 60, g);
  }
  else if (!pcAlive) strcpy(line, "PC not connected");
  else if (secsLeft < 0) strcpy(line, "Idle            ");
  else snprintf(line, sizeof line, "Left %02ld:%02ld      ", secsLeft / 60, secsLeft % 60);
  lcd.setCursor(0, 1); lcd.print(line);
}

void loop() {
  readSerial();

  float dev = fabs(readAccelG() - 1.0);
  if (dev > accelPeak) accelPeak = dev;
  if (dev > lcdPeak) lcdPeak = dev;

  if (millis() - lastConv >= 400) {
    tempC = ds.getTempCByIndex(0);
    ds.requestTemperatures();
    lastConv = millis();
  }

  bool sensorOk = tempC > -50 && tempC < 125;
  bool pcAlive = millis() - lastCmd < PC_TIMEOUT_MS;
  bool standalone = !pcAlive && STANDALONE_SETPOINT > 0;
  float target = standalone ? STANDALONE_SETPOINT : setpoint;
  if (!sensorOk || !(pcAlive || standalone) || target <= 0 || tempC >= MAX_TEMP) heater = false;
  else if (tempC < target - HYSTERESIS) heater = true;
  else if (tempC > target + HYSTERESIS) heater = false;
  digitalWrite(PIN_HEATER, heater);

  // ponytail: LCD write blocks ~20 ms on the shared I2C bus, so accel isn't sampled then; refresh less often if jumps get missed
  if (millis() - lastLcd >= 500) {
    updateLcd(sensorOk, pcAlive, standalone, target);
    lcdPeak = 0;
    lastLcd = millis();
  }

  if (millis() - lastReport >= 100) {
    Serial.print(tempC, 2); Serial.print(',');
    Serial.print(heater); Serial.print(',');
    Serial.println(accelPeak, 3);
    accelPeak = 0;
    lastReport = millis();
  }
}
