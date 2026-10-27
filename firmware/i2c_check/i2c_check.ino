// I2C diagnostic for the heat-bed Nano: checks SDA/SCL line levels, then scans.
// Open Serial Monitor at 115200. Repeats every 2 s, so you can wiggle/unplug wires live.
//
// Idle I2C lines must be HIGH (the LCD backpack and MPU board have pull-up resistors).
//   LOW  = something holds the line down: unpowered/broken module, short, or SDA/SCL mixed up.
// Expected devices: LCD 0x27 or 0x3F, MPU6050 0x68 (0x69 if its AD0 pin is high).

#include <Wire.h>

void setup() {
  Serial.begin(115200);
  Serial.println(F("I2C check - SDA must be A4, SCL must be A5"));
}

void lineState(const char *name, int pin) {
  Serial.print(name);
  Serial.print(digitalRead(pin) ? F("HIGH ok   ") : F("LOW  BAD  "));
}

void loop() {
  Wire.end();
  pinMode(A4, INPUT);  // no internal pull-up: read what the modules' pull-ups give
  pinMode(A5, INPUT);
  delay(5);
  lineState("SDA(A4)=", A4);
  lineState("SCL(A5)=", A5);
  Serial.println();

  Wire.begin();
  Wire.setWireTimeout(3000, true);  // don't hang forever if the bus is stuck
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
  if (!found) Serial.println(F("  no devices"));
  Serial.println();
  delay(2000);
}
