#include "haptic_control.h"
#include <Wire.h>
#include <Adafruit_BNO08x.h>

// Classic ESP32 DevKit pins. Change these for other ESP32 boards.
constexpr int SDA_PIN = 21;
constexpr int SCL_PIN = 22;

Adafruit_BNO08x imu(-1);  // No reset pin connected
sh2_SensorValue_t reading;

void enableReports() {
  // Orientation at 50 Hz (20,000 microseconds).
  if (!imu.enableReport(SH2_GAME_ROTATION_VECTOR, 20000)) {
    Serial.println("Failed to enable orientation reports");
  }
}

void setup() {
  setupHaptic();
  Serial.begin(115200);
  delay(1000);
  Wire.begin(SDA_PIN, SCL_PIN);

  // Try both possible BNO085 addresses.
  if (!imu.begin_I2C(0x4A, &Wire) &&
      !imu.begin_I2C(0x4B, &Wire)) {
    Serial.println("BNO085 not found. Check wiring and I2C mode.");
    while (true) {
      delay(1000);
    }
  }

  enableReports();
  Serial.println("BNO085 ready");
  Serial.println("w,x,y,z");
}

void loop() {
  serviceHaptic();
  if (imu.wasReset()) {
    enableReports();
  }

  if (imu.getSensorEvent(&reading) &&
      reading.sensorId == SH2_GAME_ROTATION_VECTOR) {
    const auto &q = reading.un.gameRotationVector;
    Serial.print(q.real, 5);
    Serial.print(',');
    Serial.print(q.i, 5);
    Serial.print(',');
    Serial.print(q.j, 5);
    Serial.print(',');
    Serial.println(q.k, 5);
  }

  serviceHaptic();
  delay(1);
}

