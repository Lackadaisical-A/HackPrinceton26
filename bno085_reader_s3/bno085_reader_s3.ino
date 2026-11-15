#include <Wire.h>
#include <SparkFun_BNO080_Arduino_Library.h>
#include <Adafruit_DRV2605.h>
#include <stdlib.h>
#include <string.h>

// Install "SparkFun BNO080 Cortex Based IMU" in Library Manager.
// Both SDA -> GPIO8, both SCL -> GPIO9, common 3.3V and GND.
// Set one sensor to 0x4A and the other to 0x4B using address pins/jumpers.
// ESP32S3 Dev Module; 16MB flash; OPI PSRAM.
// COM8 through CH343 USB/UART: CDC On Boot disabled (native USB: enabled).
// DRV2605L shares GPIO8/9 at 0x5A; motor 1 connects to its OUT+/OUT-.
// Motor 2 uses GPIO15 -> resistor -> NPN base, with a flyback diode.
const uint8_t PWM_MOTOR_PIN = 15;
Adafruit_DRV2605 haptic;
bool drvReady = false, pulseActive = false, pwmReady = false, pwmActive = false;
uint32_t pulseStarted = 0, pulseDuration = 0, pwmStarted = 0, pwmDuration = 0;
char command[64];
size_t commandLength = 0;
bool commandOverflow = false;
BNO080 sensors[2];
const uint8_t addresses[2] = {0x4A, 0x4B};
const char *names[2] = {"imu1", "imu2"};
// Optional recovery wiring. Leave disabled until the wires are connected.
// Example: imu1 RST -> GPIO6, imu2 RST -> GPIO7: change to {6, 7}.
const int8_t resetPins[2] = {-1, -1};
// Optional data-ready wiring: imu1 INT -> GPIO4, imu2 INT -> GPIO5.
// Change to {4, 5} only after wiring; 255 means no INT connection.
const uint8_t interruptPins[2] = {255, 255};
const uint32_t STALE_MS = 2000;
const uint32_t RETRY_MS = 5000;
bool ready[2] = {false, false};
bool streaming[2] = {false, false};
uint32_t lastReport[2] = {0, 0};
uint32_t lastAttempt[2] = {0, 0};

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
  stopPwmMotor(); // Stop GPIO output before any potentially slow I2C operation.
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
  const bool ack = probeDrv(); // Always probe, even if startup initialization failed.
  Serial.printf("# DEVICE S3_IO_V1 IMU1=%d IMU2=%d DRV2605L=%d MOTOR=ERM PWM2=%d PWM_PIN=%u DRV_INIT=%d DRV_ACK=%d DRV_ERR=%u DRV_REASON=%s HAPTIC_FW=2\n",
                ready[0], ready[1], drvReady && ack, pwmReady, PWM_MOTOR_PIN,
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

void startSensor(int i) {
  // begin() and optional hardware reset block. Request both motors off before
  // starting recovery, instead of postponing pulse expiration until it returns.
  stopMotor();
  ready[i] = false;
  streaming[i] = false;
  if (resetPins[i] >= 0) {
    // Open drain releases RST to the breakout's pull-up; never drive it high.
    digitalWrite(resetPins[i], LOW);
    pinMode(resetPins[i], OUTPUT_OPEN_DRAIN);
    delay(10);
    digitalWrite(resetPins[i], HIGH);
    delay(200);
    Serial.printf("# %s hardware reset\n", names[i]);
  }

  Wire.beginTransmission(addresses[i]);
  const uint8_t error = Wire.endTransmission();
  if (error != 0) {
    Serial.printf("# %s no ACK at 0x%02X (I2C error %u); retry in 5s\n",
                  names[i], addresses[i], error);
  } else {
    // Reset the library's packet sequence and cached report state as well.
    sensors[i] = BNO080{};
    ready[i] = sensors[i].begin(addresses[i], Wire, interruptPins[i]);
    if (ready[i]) {
      sensors[i].hasReset(); // Consume the reset caused by begin().
      sensors[i].enableGameRotationVector(20);
      lastReport[i] = millis();
      Serial.printf("# %s initialized at 0x%02X; waiting for data\n", names[i], addresses[i]);
    } else {
      Serial.printf("# %s ACK at 0x%02X but initialization failed; retry in 5s\n",
                    names[i], addresses[i]);
    }
  }
  lastAttempt[i] = millis();
}

void setup() {
  pinMode(PWM_MOTOR_PIN, OUTPUT);
  digitalWrite(PWM_MOTOR_PIN, LOW);
  pwmReady = ledcAttach(PWM_MOTOR_PIN, 20000, 8);
  if (pwmReady) pwmReady = ledcWrite(PWM_MOTOR_PIN, 0);
  Serial.begin(115200);
  delay(1500);
  Wire.begin(8, 9);
  Wire.setClock(100000);
  Wire.setTimeOut(100); // Bound individual transactions; allow clock stretching.
  initializeDrv();
  Serial.println("# S3_DUAL_IMU_V3 two motor outputs; automatic recovery enabled");
  for (int i = 0; i < 2; ++i) {
    startSensor(i);
  }
  reportStatus();
  Serial.println("# sensor_id,w,x,y,z");
}

void loop() {
  serviceCommands();
  serviceDrvRecovery();
  for (int i = 0; i < 2; ++i) {
    serviceCommands();
    if (!ready[i]) continue;
    const uint16_t report = sensors[i].getReadings();
    expirePulse();
    if (sensors[i].hasReset()) {
      stopMotor();
      sensors[i].enableGameRotationVector(20);
      Serial.printf("# IMU RESET %s; motors stopped; reports re-enabled\n", names[i]);
    }
    if (report != SENSOR_REPORTID_GAME_ROTATION_VECTOR) continue;
    lastReport[i] = millis();
    if (!streaming[i]) {
      streaming[i] = true;
      Serial.printf("# %s streaming at 0x%02X\n", names[i], addresses[i]);
    }
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
  // Service both sensors before attempting recovery. begin() is blocking and
  // can briefly interrupt the healthy stream, so recover at most one per loop.
  for (int i = 0; i < 2; ++i) {
    const uint32_t now = millis();
    if (ready[i] && now - lastReport[i] >= STALE_MS) {
      ready[i] = false;
      Serial.printf("# %s stale at 0x%02X; attempting recovery\n", names[i], addresses[i]);
      lastAttempt[i] = now - RETRY_MS; // First attempt immediately on timeout.
    }
    if (!ready[i] && now - lastAttempt[i] >= RETRY_MS) {
      Serial.printf("# IMU RECOVERY %s; stopping motors\n", names[i]);
      startSensor(i);
      break;
    }
  }
  serviceCommands();
  delay(1);
}
