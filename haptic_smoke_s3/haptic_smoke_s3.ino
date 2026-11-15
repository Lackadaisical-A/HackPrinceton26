#include "haptic_control.h"
#include <Wire.h>
#include <SparkFun_BNO080_Arduino_Library.h>

// Install "SparkFun BNO080 Cortex Based IMU" in Library Manager.
// Both SDA -> GPIO8, both SCL -> GPIO9, common 3.3V and GND.
// Set one sensor to 0x4A and the other to 0x4B using address pins/jumpers.
// ESP32S3 Dev Module; 16MB flash; OPI PSRAM; native USB: CDC On Boot enabled.
BNO080 sensors[2];
const uint8_t addresses[2] = {0x4A, 0x4B};
const char *names[2] = {"imu1", "imu2"};
bool ready[2] = {false, false};

void setup() {
  setupHaptic();
  Serial.begin(115200);
  delay(1500);
  Wire.begin(8, 9);
  Wire.setClock(100000);
  for (int i = 0; i < 2; ++i) {
    Wire.beginTransmission(addresses[i]);
    if (Wire.endTransmission() != 0) {
      Serial.printf("# %s missing at 0x%02X; check address and wiring\n", names[i], addresses[i]);
      continue;
    }
    ready[i] = sensors[i].begin(addresses[i], Wire);
    if (ready[i]) {
      sensors[i].enableGameRotationVector(20);
      Serial.printf("# %s ready at 0x%02X\n", names[i], addresses[i]);
    } else {
      Serial.printf("# %s initialization failed\n", names[i]);
    }
  }
  Serial.println("# sensor_id,w,x,y,z");
}

void loop() {
  serviceHaptic();
  for (int i = 0; i < 2; ++i) {
    if (!ready[i]) continue;
    const uint16_t report = sensors[i].getReadings();
    if (sensors[i].hasReset()) sensors[i].enableGameRotationVector(20);
    if (report != SENSOR_REPORTID_GAME_ROTATION_VECTOR) continue;
    Serial.print(names[i]);
    Serial.print(',');
    Serial.print(sensors[i].getQuatReal(), 5);
    Serial.print(',');
    Serial.print(sensors[i].getQuatI(), 5);
    Serial.print(',');
    Serial.print(sensors[i].getQuatJ(), 5);
    Serial.print(',');
    Serial.println(sensors[i].getQuatK(), 5);
  }
  serviceHaptic();
  delay(1);
}

