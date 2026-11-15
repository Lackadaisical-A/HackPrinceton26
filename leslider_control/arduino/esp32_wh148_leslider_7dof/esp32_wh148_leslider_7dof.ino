/*
  Seven-pot WH148 leader for SO-101 + a seventh slider servo.

  Potentiometer wiring (viewed electrically):
    outer leg -> 3V3
    center/wiper -> GPIO listed below
    outer leg -> GND

  Serial CSV order at 115200 baud:
    GPIO32 shoulder_pan
    GPIO33 shoulder_lift
    GPIO34 elbow_flex
    GPIO35 wrist_flex
    GPIO25 wrist_roll
    GPIO26 gripper
    GPIO27 slider left/right

  GPIO25, GPIO26, and GPIO27 are ADC2 pins on the classic ESP32. Do not enable
  Wi-Fi in this sketch because Wi-Fi competes with ADC2. The Python bridge is:
    .\control.ps1 leslider -LeaderPort YOUR_POT_ESP32_PORT
*/

#include <Arduino.h>

#if !defined(CONFIG_IDF_TARGET_ESP32)
#error "Select ESP32 Dev Module (classic ESP32/WROOM); this ADC pin map is not for ESP32-S3."
#endif

constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint8_t POT_PINS[] = {32, 33, 34, 35, 25, 26, 27};
constexpr size_t POT_COUNT = sizeof(POT_PINS) / sizeof(POT_PINS[0]);
constexpr uint8_t ADC_SAMPLES = 4;
constexpr uint32_t FRAME_INTERVAL_US = 10000;  // 100 Hz sensor stream.

uint32_t nextFrameUs = 0;

uint16_t readAveraged(const uint8_t pin) {
  uint32_t sum = 0;
  for (uint8_t sample = 0; sample < ADC_SAMPLES; ++sample) {
    sum += analogRead(pin);
    delayMicroseconds(250);
  }
  return static_cast<uint16_t>(sum / ADC_SAMPLES);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
#if defined(ARDUINO_ARCH_ESP32)
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
#endif
  for (const uint8_t pin : POT_PINS) {
    pinMode(pin, INPUT);
  }
  delay(250);
  nextFrameUs = micros();
}

void loop() {
  const uint32_t now = micros();
  if (static_cast<int32_t>(now - nextFrameUs) < 0) {
    return;
  }
  nextFrameUs = now + FRAME_INTERVAL_US;

  for (size_t index = 0; index < POT_COUNT; ++index) {
    if (index != 0) {
      Serial.print(',');
    }
    Serial.print(readAveraged(POT_PINS[index]));
  }
  Serial.println();
}
