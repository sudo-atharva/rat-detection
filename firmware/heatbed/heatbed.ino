// Arduino Nano: heat bed bang-bang control + DS18B20 + MPU6050 jump sensing.
// Libraries: OneWire, DallasTemperature, LiquidCrystal_I2C (Frank de Brabander) (Library Manager).
//
// Wiring:
//   DS18B20 data -> D2 (4.7k pull-up to 5V)
//   MOSFET module SIG/PWM -> D9, its signal GND -> Nano GND (active HIGH = bed on)
//     MK2B bed (12V wiring, ~1.1 ohm = ~11 A) on the module's load terminals, fed by the 12V SMPS.
//     Use a logic-level module rated >= 15 A (e.g. D4184 dual-MOSFET); IRF520 modules overheat at this current.
//   MPU6050 SDA  -> A4, SCL -> A5, VCC -> 5V (or 3.3V per breakout), GND -> GND
//   LCD 16x2 I2C backpack: SDA -> A4, SCL -> A5 (shared with MPU), VCC -> 5V, GND -> GND
//
// Start menu (Serial Monitor 115200, type the number + Enter):
//   1 = main program (default after MENU_WAIT_MS, or as soon as the GUI sends a command)
//   2 = heat test without PC: heats to TEST_SETPOINT, LCD/Serial show temp + shake
//   3 = I2C check: line levels + device scan every 2 s
//
// Serial 115200 (main program):
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
const float HYSTERESIS = 0.5;      // C: heater ON below setpoint-0.5, OFF above setpoint+0.5
// PWM duty while heating, 0-255. MK2B gives ~130 W at full power, far more than 37 C needs,
// so it overshoots; lower this (e.g. 100 = ~40%) if temp swings well past the setpoint.
const byte HEATER_POWER = 255;
const unsigned long PC_TIMEOUT_MS = 5000;  // LCD shows "PC not connected" after this (heater keeps running)
const int MPU = 0x68;
const int LCD_ADDR = 0x27;  // 0x3F on some backpacks; menu option 3 shows which one you have
const float TEST_SETPOINT = 37.0;  // menu option 2
const unsigned long MENU_WAIT_MS = 8000;

enum Mode { MODE_MAIN = 1, MODE_HEAT_TEST = 2, MODE_I2C_CHECK = 3 };

OneWire oneWire(PIN_ONEWIRE);
DallasTemperature ds(&oneWire);
LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);

byte mode = MODE_MAIN;
float setpoint = 0;
float tempC = DEVICE_DISCONNECTED_C;
bool heater = false;
float accelPeak = 0;
float lcdPeak = 0;  // biggest shake since last LCD refresh (heat test display)
long secsLeft = -1;
unsigned long lastConv = 0, lastReport = 0, lastCmd = 0, lastLcd = 0;
char buf[16];
byte bufLen = 0;

// Waits for 1/2/3 on Serial. A GUI command ('S'/'T') means main program; its first
// character is kept in buf so readSerial() still gets the whole command.
byte chooseMode() {
  Serial.println(F("\nRat heat bed - choose mode, type number + Enter:"));
  Serial.println(F("  1 = main program (used by the GUI)"));
  Serial.println(F("  2 = heat test without PC (heats to TEST_SETPOINT)"));
  Serial.println(F("  3 = I2C check (LCD + MPU6050)"));
  Serial.print(F("Starting main program in ")); Serial.print(MENU_WAIT_MS / 1000); Serial.println(F(" s..."));
  lcd.setCursor(0, 0); lcd.print(F("1Main 2Heat 3I2C"));
  unsigned long t0 = millis();
  while (millis() - t0 < MENU_WAIT_MS) {
    if (Serial.available()) {
      char c = Serial.read();
      if (c >= '1' && c <= '3') return c - '0';
      if (c == 'S' || c == 'T') {
        buf[0] = c;
        bufLen = 1;
        return MODE_MAIN;
      }
    }
    lcd.setCursor(0, 1);
    lcd.print(F("Main in "));
    lcd.print((MENU_WAIT_MS - (millis() - t0)) / 1000 + 1);
    lcd.print(F("s   "));
    delay(50);
  }
  return MODE_MAIN;
}

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
  Wire.setWireTimeout(3000, true);   // a stuck I2C bus must not freeze heater control
  Wire.beginTransmission(MPU); Wire.write(0x6B); Wire.write(0); Wire.endTransmission();     // wake
  Wire.beginTransmission(MPU); Wire.write(0x1C); Wire.write(0x10); Wire.endTransmission();  // +-8g

  lcd.init();
  lcd.backlight();

  mode = chooseMode();
  lcd.clear();
  Serial.print(F("Mode ")); Serial.println(mode);
  if (mode == MODE_HEAT_TEST) {
    Serial.print(F("Heat test: heating to ")); Serial.print(TEST_SETPOINT, 1);
    Serial.println(F(" C. Tap the bed to test the MPU. Reset the Nano to leave."));
  }
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
        setpoint = atof(buf + 1);
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

// Row 0: "T:36.50C S:37.0*" (* = heater on). Row 1: run time left (main) or uptime + shake (heat test).
void updateLcd(bool sensorOk, bool pcAlive, float target) {
  char t[8], s[8], line[17];
  if (sensorOk) dtostrf(tempC, 5, 2, t); else strcpy(t, " ERR ");
  dtostrf(target, 4, 1, s);
  snprintf(line, sizeof line, "T:%sC S:%s%c", t, s, heater ? '*' : ' ');
  lcd.setCursor(0, 0); lcd.print(line);
  if (mode == MODE_HEAT_TEST) {
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

void i2cCheck() {
  Wire.end();
  pinMode(A4, INPUT);  // no internal pull-up: read what the modules' pull-ups give
  pinMode(A5, INPUT);
  delay(5);
  Serial.print(F("SDA(A4)=")); Serial.print(digitalRead(A4) ? F("HIGH ok   ") : F("LOW  BAD  "));
  Serial.print(F("SCL(A5)=")); Serial.println(digitalRead(A5) ? F("HIGH ok") : F("LOW  BAD"));

  Wire.begin();
  Wire.setWireTimeout(3000, true);
  byte found = 0;
  for (byte addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("  found 0x"));
      if (addr < 16) Serial.print('0');
      Serial.print(addr, HEX);
      if (addr == 0x27 || addr == 0x3F) Serial.print(F("  <- LCD"));
      if (addr == 0x68 || addr == 0x69) Serial.print(F("  <- MPU6050"));
      Serial.println();
      found++;
    }
  }
  if (Wire.getWireTimeoutFlag()) {
    Serial.println(F("  bus TIMEOUT - a device is holding the lines; unplug modules one by one"));
    Wire.clearWireTimeoutFlag();
  }
  if (!found) Serial.println(F("  no devices - LOW lines: a module holds them down; HIGH lines: SDA/SCL swapped?"));
  Serial.println();
  delay(2000);
}

void loop() {
  if (mode == MODE_I2C_CHECK) {
    i2cCheck();
    return;
  }
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
  bool test = mode == MODE_HEAT_TEST;
  bool pcAlive = millis() - lastCmd < PC_TIMEOUT_MS;
  float target = test ? TEST_SETPOINT : setpoint;
  if (tempC < target - HYSTERESIS) heater = true;
  else if (tempC > target + HYSTERESIS) heater = false;
  analogWrite(PIN_HEATER, heater ? HEATER_POWER : 0);  // D9 = hardware PWM, ~490 Hz

  // ponytail: LCD write blocks ~20 ms on the shared I2C bus, so accel isn't sampled then; refresh less often if jumps get missed
  if (millis() - lastLcd >= 500) {
    updateLcd(sensorOk, pcAlive, target);
    if (test) {  // human-readable in heat test; the GUI's CSV format otherwise
      Serial.print(F("temp ")); Serial.print(tempC, 2);
      Serial.print(F(" C   heater ")); Serial.print(heater ? F("ON ") : F("off"));
      Serial.print(F("   shake ")); Serial.print(lcdPeak, 2); Serial.println(F(" g"));
    }
    lcdPeak = 0;
    lastLcd = millis();
  }

  if (!test && millis() - lastReport >= 100) {
    Serial.print(tempC, 2); Serial.print(',');
    Serial.print(heater); Serial.print(',');
    Serial.println(accelPeak, 3);
    accelPeak = 0;
    lastReport = millis();
  }
}
