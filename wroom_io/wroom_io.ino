#include <Wire.h>
#include <Adafruit_BNO08x.h>
#include <Adafruit_DRV2605.h>
#include <stdlib.h>
#include <string.h>

// WROOM hardware interface. Motion rules live in Python, not on this board.
// Both devices: SDA GPIO21, SCL GPIO22, common GND, 3.3V logic.
// Coin motor connects to DRV2605L OUT+/OUT-. This config is for a DC/ERM coin.
// A second DC/ERM motor uses an NPN low-side switch on GPIO15 (NOT I2C).
// See README.md for transistor pinout, supply, resistor and flyback diode wiring.
const uint8_t PWM_MOTOR_PIN = 15;
// Joystick powered from 3.3V. ADC2 pins: do not enable WiFi on this firmware.
// GPIO2 can prevent flashing if driven high; disconnect VRx during upload.
const uint8_t JOYSTICK_X_PIN = 2, JOYSTICK_Y_PIN = 4, JOYSTICK_SW_PIN = 5;
uint32_t joystickReported = 0, switchChanged = 0;
bool switchRaw = false, switchPressed = false;
Adafruit_BNO08x imu(-1);
Adafruit_DRV2605 haptic;
sh2_SensorValue_t reading;
bool imuReady = false, drvReady = false, pulseActive = false;
uint32_t pulseStarted = 0, pulseDuration = 0;
bool pwmReady = false, pwmActive = false;
uint32_t pwmStarted = 0, pwmDuration = 0;
char command[64];
size_t commandLength = 0;
bool commandOverflow = false;

bool devicePresent(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

const uint32_t DRV_RETRY_MS = 3000;
uint32_t lastDrvAttempt = 0;
uint8_t drvProbeError = 255;
const char *drvReason = "STARTUP";

bool probeDrv() {
  Wire.beginTransmission(0x5A);
  drvProbeError = Wire.endTransmission();
  return drvProbeError == 0;
}

// The library's readRegister8() discards I2C errors. Check them explicitly
// when deciding whether configuration succeeded.
bool readDrvRegister(uint8_t reg, uint8_t &value) {
  Wire.beginTransmission(0x5A);
  Wire.write(reg);
  drvProbeError = Wire.endTransmission(false);
  if (drvProbeError != 0) return false;
  if (Wire.requestFrom(uint8_t(0x5A), uint8_t(1)) != 1) {
    drvProbeError = 6; // Local diagnostic: register read returned no byte.
    return false;
  }
  value = Wire.read();
  return true;
}

bool initializeDrv() {
  lastDrvAttempt = millis();
  drvReady = false;
  pulseActive = false;
  if (!probeDrv()) {
    drvReason = "NO_ACK";
    return false;
  }
  if (!haptic.begin(&Wire)) {
    drvReason = "BEGIN_FAILED";
    return false;
  }
  // Recovery always starts at zero; no previously requested vibration resumes.
  haptic.stop();
  haptic.setRealtimeValue(0);
  haptic.useERM();
  uint8_t control = 0;
  if (!readDrvRegister(DRV2605_REG_CONTROL3, control)) {
    drvReason = "CONFIG_READ";
    return false;
  }
  // DC/ERM open loop, signed RTP: 0 off, 1..127 forward amplitude.
  haptic.writeRegister8(DRV2605_REG_CONTROL3, (control | 0x20) & ~0x08);
  haptic.setMode(DRV2605_MODE_REALTIME);
  uint8_t mode = 0, rtp = 255, feedback = 0x80;
  bool verified = readDrvRegister(DRV2605_REG_MODE, mode) &&
                  readDrvRegister(DRV2605_REG_RTPIN, rtp) &&
                  readDrvRegister(DRV2605_REG_CONTROL3, control) &&
                  readDrvRegister(DRV2605_REG_FEEDBACK, feedback) &&
                  mode == DRV2605_MODE_REALTIME && rtp == 0 &&
                  (control & 0x28) == 0x20 && (feedback & 0x80) == 0;
  if (!verified) {
    haptic.setRealtimeValue(0);
    haptic.stop();
    drvReason = "CONFIG_VERIFY";
    return false;
  }
  drvReady = true;
  drvReason = "READY";
  return true;
}

void serviceDrvRecovery() {
  // Don't run library initialization in the middle of either motor's pulse.
  if (pulseActive || pwmActive || uint32_t(millis() - lastDrvAttempt) < DRV_RETRY_MS) return;
  lastDrvAttempt = millis();
  if (!probeDrv()) {
    drvReady = false;
    drvReason = "NO_ACK";
    return;
  }
  uint8_t mode = 0;
  if (drvReady && readDrvRegister(DRV2605_REG_MODE, mode) &&
      mode == DRV2605_MODE_REALTIME) return;
  // Also reconfigure after a driver-only power cycle (mode returns to standby).
  if (initializeDrv()) Serial.println("# DRV2605L ready after retry; output remains OFF");
}


void stopDrvMotor() {
  if (drvReady) haptic.setRealtimeValue(0);
  pulseActive = false;
}

void stopPwmMotor() {
  if (pwmReady) ledcWrite(PWM_MOTOR_PIN, 0);
  pwmActive = false;
}

void stopMotor() {
  stopPwmMotor();
  stopDrvMotor();
}

void expirePulse() {
  if (pwmActive && uint32_t(millis() - pwmStarted) >= pwmDuration) {
    stopPwmMotor();
    Serial.println("# HAPTIC2 OFF");
  }
  if (pulseActive && uint32_t(millis() - pulseStarted) >= pulseDuration) {
    stopDrvMotor();
    Serial.println("# HAPTIC OFF");
  }
}

void reportStatus() {
  // Preserve the existing identity/fields for dashboard and motion clients.
  const bool ack = probeDrv(); // Always probe, even if startup initialization failed.
  Serial.printf("# DEVICE WROOM_IO_V1 IMU=%d DRV2605L=%d MOTOR=ERM PWM2=%d PWM_PIN=%u JOYSTICK=1 X_PIN=2 Y_PIN=4 SW_PIN=5 DRV_INIT=%d DRV_ACK=%d DRV_ERR=%u DRV_REASON=%s HAPTIC_FW=2\n",
                imuReady, drvReady && ack, pwmReady, PWM_MOTOR_PIN,
                drvReady, ack, drvProbeError, drvReason);
}

bool parseNumber(const char *value, long &number) {
  if (!value || !*value) return false;
  char *end = nullptr;
  number = strtol(value, &end, 10);
  return *end == '\0';
}

void executeCommand() {
  command[commandLength] = '\0';
  if (!strcmp(command, "PING")) {
    Serial.println("# PONG");
  } else if (!strcmp(command, "STATUS")) {
    reportStatus();
  } else if (!strcmp(command, "STOP")) {
    stopMotor();
    Serial.println("# HAPTIC STOPPED");
  } else if (!strcmp(command, "STOP1")) {
    stopDrvMotor();
    Serial.println("# HAPTIC STOPPED");
  } else if (!strcmp(command, "STOP2")) {
    stopPwmMotor();
    Serial.println("# HAPTIC2 STOPPED");
  } else if (!strncmp(command, "VIBRATE2,", 9)) {
    // VIBRATE2,<milliseconds>,<PWM duty 0..255>. Renew to keep vibrating.
    char *durationText = command + 9;
    char *strengthText = strchr(durationText, ',');
    long duration = 0, strength = 0;
    if (strengthText) *strengthText++ = '\0';
    if (!parseNumber(durationText, duration) || duration < 1 || duration > 500 ||
        !parseNumber(strengthText, strength) || strength < 0 || strength > 255) {
      Serial.println("# ERROR use VIBRATE2,1..500,0..255");
    } else if (!pwmReady) {
      Serial.println("# ERROR PWM2 not ready");
    } else if (!ledcWrite(PWM_MOTOR_PIN, uint8_t(strength))) {
      stopPwmMotor();
      Serial.println("# ERROR PWM2 write failed");
    } else {
      pwmStarted = millis();
      pwmDuration = uint32_t(duration);
      pwmActive = strength > 0;
      Serial.printf("# HAPTIC2 ON %ld %ld\n", duration, strength);
    }
  } else if (!strncmp(command, "VIBRATE,", 8)) {
    // VIBRATE,<milliseconds>[,<strength 1..127>]. Default strength 64.
    char *durationText = command + 8;
    char *strengthText = strchr(durationText, ',');
    long duration = 0, strength = 64;
    if (strengthText) *strengthText++ = '\0';
    if (!parseNumber(durationText, duration) || duration < 1 || duration > 500 ||
        (strengthText && !parseNumber(strengthText, strength)) || strength < 1 || strength > 127) {
      Serial.println("# ERROR use VIBRATE,1..500,1..127");
    } else if (!drvReady || !devicePresent(0x5A)) {
      stopDrvMotor();
      Serial.println("# ERROR DRV2605L not ready; inspect DRV_INIT/DRV_ACK in STATUS; idle retry every 3s");
    } else {
      pulseStarted = millis();
      pulseDuration = uint32_t(duration);
      haptic.setRealtimeValue(uint8_t(strength));
      if (haptic.readRegister8(DRV2605_REG_RTPIN) != uint8_t(strength)) {
        stopDrvMotor();
        Serial.println("# ERROR DRV2605L command readback failed");
        return;
      }
      pulseActive = true;
      Serial.printf("# HAPTIC ON %ld\n", duration);
    }
  } else {
    Serial.println("# ERROR use STATUS, PING, VIBRATE,150,64, VIBRATE2,150,128, STOP1, STOP2 or STOP");
  }
}

void serviceCommands() {
  expirePulse();
  for (int budget = 0; budget < 64 && Serial.available(); ++budget) {
    char c = char(Serial.read());
    if (c == '\r') continue;
    if (c == '\n') {
      if (commandOverflow) Serial.println("# ERROR command too long");
      else if (commandLength) executeCommand();
      commandLength = 0;
      commandOverflow = false;
    } else if (commandLength < sizeof(command) - 1 && !commandOverflow) {
      command[commandLength++] = c;
    } else {
      commandOverflow = true;
    }
  }
  expirePulse();
}

void serviceJoystick() {
  const uint32_t now = millis();
  const bool pressed = digitalRead(JOYSTICK_SW_PIN) == LOW;
  if (pressed != switchRaw) {
    switchRaw = pressed;
    switchChanged = now;
  }
  if (uint32_t(now - switchChanged) >= 30) switchPressed = switchRaw;
  if (uint32_t(now - joystickReported) < 20) return;
  joystickReported = now;
  uint32_t x = 0, y = 0;
  for (int i = 0; i < 4; ++i) {
    x += analogRead(JOYSTICK_X_PIN);
    y += analogRead(JOYSTICK_Y_PIN);
  }
  // Separate from the unchanged quaternion CSV; movement rules stay on the PC.
  Serial.printf("# JOYSTICK,%lu,%lu,%lu,%d\n",
                (unsigned long)now, (unsigned long)(x / 4),
                (unsigned long)(y / 4), switchPressed);
}

void setup() {
  // Off before sensor initialization. Arduino-ESP32 3.x LEDC API.
  // GPIO15 has a boot pull-up: use a base pulldown; GPIO18 is an alternative.
  pinMode(PWM_MOTOR_PIN, OUTPUT);
  digitalWrite(PWM_MOTOR_PIN, LOW);
  pwmReady = ledcAttach(PWM_MOTOR_PIN, 20000, 8);
  if (pwmReady) pwmReady = ledcWrite(PWM_MOTOR_PIN, 0);
  pinMode(JOYSTICK_X_PIN, INPUT);
  pinMode(JOYSTICK_Y_PIN, INPUT);
  pinMode(JOYSTICK_SW_PIN, INPUT_PULLUP);
  analogReadResolution(12);
  analogSetPinAttenuation(JOYSTICK_X_PIN, ADC_11db);
  analogSetPinAttenuation(JOYSTICK_Y_PIN, ADC_11db);
  switchRaw = switchPressed = digitalRead(JOYSTICK_SW_PIN) == LOW;
  Serial.begin(115200);
  delay(1000);
  Wire.begin(21, 22);
  Wire.setClock(100000);
  Wire.setTimeOut(25);
  initializeDrv();
  imuReady = imu.begin_I2C(0x4A, &Wire) || imu.begin_I2C(0x4B, &Wire);
  if (imuReady) imuReady = imu.enableReport(SH2_GAME_ROTATION_VECTOR, 20000);
  if (imuReady) imu.wasReset();  // Consume the expected startup reset notification.
  reportStatus();
  Serial.println("# w,x,y,z");
}

void loop() {
  serviceCommands();
  serviceDrvRecovery();
  serviceJoystick();
  if (imuReady) {
    if (imu.wasReset()) {
      stopMotor();
      imuReady = imu.enableReport(SH2_GAME_ROTATION_VECTOR, 20000);
      Serial.println("# IMU RESET");
    }
    if (imu.getSensorEvent(&reading) && reading.sensorId == SH2_GAME_ROTATION_VECTOR) {
      const auto &q = reading.un.gameRotationVector;
      Serial.printf("%.5f,%.5f,%.5f,%.5f\n", q.real, q.i, q.j, q.k);
    }
  }
  serviceCommands();
  delay(1);
}
