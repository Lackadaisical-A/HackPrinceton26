/*
  ESP32 WH148 leader arm reader for SO-100/SO-101.

  Serial output at 115200 baud is one CSV row per sample:
    shoulder_pan,shoulder_lift,elbow_flex,wrist_flex,wrist_roll,gripper

  Default GPIO order:
    32 shoulder_pan
    33 shoulder_lift
    34 elbow_flex
    35 wrist_flex
    25 wrist_roll
    26 gripper

  Wire each potentiometer with one outer leg to 3V3, the other outer leg to
  GND, and the center/wiper leg to its GPIO. Never put 5 V on an ESP32 ADC pin.

  GPIO 25 and 26 use ADC2 on a classic ESP32. This USB-serial sketch does not
  enable Wi-Fi because Wi-Fi and ADC2 cannot be used together on that chip.
*/

#include <Arduino.h>

#if !defined(CONFIG_IDF_TARGET_ESP32)
#error "Select ESP32 Dev Module (classic ESP32/WROOM); this ADC pin map is not for ESP32-S3."
#endif

constexpr int kPotPins[] = {32, 33, 34, 35, 25, 26};
constexpr int kChannelCount = sizeof(kPotPins) / sizeof(kPotPins[0]);
constexpr int kReadsPerSample = 4;
// Four averaged reads across six channels take about 6-7 ms. A 10 ms pause
// produces roughly 60 complete six-joint frames per second over USB serial.
constexpr int kSampleDelayMs = 10;

int readAveragedAnalog(int pin) {
  long total = 0;
  for (int i = 0; i < kReadsPerSample; ++i) {
    total += analogRead(pin);
    delayMicroseconds(250);
  }
  return static_cast<int>(total / kReadsPerSample);
}

void setup() {
  Serial.begin(115200);

#if defined(ARDUINO_ARCH_ESP32)
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
#endif

  for (int i = 0; i < kChannelCount; ++i) {
    pinMode(kPotPins[i], INPUT);
  }

#if defined(LED_BUILTIN)
  pinMode(LED_BUILTIN, OUTPUT);
#endif
}

void loop() {
  for (int i = 0; i < kChannelCount; ++i) {
    Serial.print(readAveragedAnalog(kPotPins[i]));
    if (i < kChannelCount - 1) {
      Serial.print(",");
    }
  }
  Serial.println();

#if defined(LED_BUILTIN)
  digitalWrite(LED_BUILTIN, !digitalRead(LED_BUILTIN));
#endif

  delay(kSampleDelayMs);
}
