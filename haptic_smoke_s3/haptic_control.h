#pragma once
#include <Arduino.h>
#include <stdlib.h>
#include <string.h>

// Active-HIGH input of an ERM motor driver / MOSFET, NEVER a bare motor.
// For an LRA coin use its matching haptic driver instead of this interface.
constexpr int HAPTIC_PIN = 18;
bool hapticActive = false;
uint32_t hapticStarted = 0;
uint32_t hapticDuration = 0;
char hapticCommand[48];
size_t hapticLength = 0;
bool hapticOverflow = false;

void stopHaptic() {
  digitalWrite(HAPTIC_PIN, LOW);
  hapticActive = false;
}

void setupHaptic() {
  digitalWrite(HAPTIC_PIN, LOW);
  pinMode(HAPTIC_PIN, OUTPUT);
}

void updateHaptic() {
  if (hapticActive && uint32_t(millis() - hapticStarted) >= hapticDuration) {
    stopHaptic();
    Serial.println("# HAPTIC OFF");
  }
}

void executeHapticCommand() {
  hapticCommand[hapticLength] = '\0';
  if (strcmp(hapticCommand, "STOP") == 0) {
    stopHaptic();
    Serial.println("# HAPTIC STOPPED");
  } else if (strncmp(hapticCommand, "VIBRATE,", 8) == 0) {
    char *end = nullptr;
    const char *value = hapticCommand + 8;
    long duration = strtol(value, &end, 10);
    if (*value && *end == '\0' && duration >= 1 && duration <= 500) {
      hapticDuration = uint32_t(duration);
      hapticStarted = millis();
      hapticActive = true;
      digitalWrite(HAPTIC_PIN, HIGH);
      Serial.printf("# HAPTIC ON %ld\n", duration);
    } else {
      Serial.println("# ERROR duration must be 1..500 ms");
    }
  } else if (strcmp(hapticCommand, "PING") == 0) {
    Serial.println("# PONG");
  } else {
    Serial.println("# ERROR use PING, VIBRATE,150 or STOP");
  }
}

void serviceHaptic() {
  updateHaptic();
  // Bounded parsing; never wait for a full serial line.
  for (int n = 0; n < 64 && Serial.available(); ++n) {
    char c = char(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') {
      if (hapticOverflow) Serial.println("# ERROR command too long");
      else if (hapticLength) executeHapticCommand();
      hapticLength = 0;
      hapticOverflow = false;
    } else if (hapticLength < sizeof(hapticCommand) - 1 && !hapticOverflow) {
      hapticCommand[hapticLength++] = c;
    } else {
      hapticOverflow = true;
    }
  }
  updateHaptic();
}
