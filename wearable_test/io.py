"""One serial owner per board, telemetry parsing and bounded haptic commands."""
from collections import deque
from copy import deepcopy
import math
import logging
import threading
import time

from so101_sim.mapper import normalize, multiply

BOARD_SENSORS = {"wroom": ("wroom",), "s3": ("s3_imu1", "s3_imu2")}
DEVICE = {"wroom": "WROOM_IO_V1", "s3": "S3_IO_V1"}


def driver_status(board, now):
    """Don't call a cached initialization failure a physically missing driver."""
    fields = board["fields"]
    if not board["connected"]:
        return "board offline"
    if now-board["status_at"] >= 6 or "DRV2605L" not in fields:
        return "waiting for device status"
    if fields["DRV2605L"] == "1":
        return "ready at 0x5A"
    if fields.get("DRV_ACK") == "0":
        return f"no I2C response at 0x5A (error {fields.get('DRV_ERR', '?')})"
    if fields.get("DRV_ACK") == "1":
        return f"I2C responds; init not ready ({fields.get('DRV_REASON', 'retrying')})"
    return "not ready; old firmware cannot distinguish init failure from no I2C response"


class Board:
    """Protocol state; caller serializes access. Clock is explicit for tests."""
    def __init__(self, name, port):
        self.name, self.port = name, port
        self.connected = False
        self.epoch = 0
        self.reset_reason = ""
        self.fields = {}
        self.status_at = -math.inf
        self.next_status = 0
        self.status_requested_at = -math.inf
        self.message = "Waiting for port"
        self.log = deque(maxlen=12)
        self.joystick = None
        self.samples = {key: {"q": None, "seen": 0., "sequence": 0, "times": deque(maxlen=100)}
                        for key in BOARD_SENSORS[name]}
        self.motors = {i: {"percent": 0, "until": 0., "next": 0., "pending": None,
                           "running": False, "acks": 0} for i in (1, 2)}
        self.stop_pending = set()

    def note(self, message):
        self.message = message
        if not self.log or self.log[-1] != message:
            self.log.append(message)

    def reset(self, message):
        self.epoch += 1
        self.reset_reason = message
        logging.getLogger("wearable").info("%s / %s reset #%d: %s",self.port,self.name,self.epoch,message)
        self.stop()
        self.fields = {}
        self.status_at = -math.inf
        self.next_status = 0
        self.joystick = None
        for sample in self.samples.values():
            sample.update(q=None, seen=0.)
            sample["times"].clear()
        self.note(message)

    def stop(self, motor=None):
        for i in ((motor,) if motor else (1, 2)):
            self.motors[i].update(percent=0, until=0., pending=None, running=False)
            self.stop_pending.add(i)

    def available(self, motor, now):
        key = "DRV2605L" if motor == 1 else "PWM2"
        return self.connected and now-self.status_at < 6 and self.fields.get(key) == "1"

    def start(self, motor, percent, seconds, now):
        if motor not in (1, 2) or not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("Motor intensity must be 0-100%")
        if not math.isfinite(seconds) or not .1 <= seconds <= 10:
            raise ValueError("A test can run for at most 10 seconds")
        if percent == 0:
            self.stop(motor)
            return
        if not self.available(motor, now):
            raise ValueError(f"{self.port}: motor {motor} is unavailable; check firmware/status and wiring")
        self.motors[motor].update(percent=percent, until=now+seconds, next=0., acks=0)
        self.note(f"Motor {motor}: {percent:.0f}% for {seconds:g}s")

    def set_intensity(self, motor, percent):
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise ValueError("Motor intensity must be 0-100%")
        if percent == 0:
            self.stop(motor)
        elif self.motors[motor]["percent"]:
            self.motors[motor]["percent"] = percent

    def commands(self, now):
        """Return commands to send now; renew only after preceding ACK."""
        for i, motor in self.motors.items():
            if not motor["percent"]:
                continue
            if not self.available(i, now):
                self.stop()
                self.note("Motor test stopped: device status missing or driver unavailable")
                break
            if motor["pending"] is not None and now >= motor["pending"]:
                self.stop()
                self.note("Motor test stopped: acknowledgment timed out")
                break
            if now >= motor["until"]:
                self.stop(i)
        result = [f"STOP{i}" for i in sorted(self.stop_pending)]
        self.stop_pending.clear()
        for i, motor in self.motors.items():
            if motor["percent"] and motor["pending"] is None and now >= motor["next"]:
                strength = max(1, round(motor["percent"]*(127 if i == 1 else 255)/100))
                result.append(f"{'VIBRATE' if i == 1 else 'VIBRATE2'},500,{strength}")
                motor.update(pending=now+.3, next=now+.1, running=True)
        if now >= self.next_status:
            result.append("STATUS")
            self.status_requested_at = now
            self.next_status = now+(3 if self.fields else .7)
        return result

    def ingest(self, line, now):
        if line.startswith("# DEVICE "):
            parts = line.split()
            if len(parts) < 3 or parts[2] != DEVICE[self.name]:
                self.reset("Wrong board on this port; check --wroom/--s3 mapping")
                return
            # A delayed STATUS response is not evidence of a reboot. Use actual
            # startup/reset notices or WROOM uptime, preserving calibration on jitter.
            self.fields = dict(part.split("=", 1) for part in parts[3:] if "=" in part)
            self.status_at = now
            return
        if line.startswith(("# IMU RESET", "# IMU RECOVERY")):
            self.reset(line[2:])
            return
        if line.startswith("# S3_DUAL_IMU_") or line == "# w,x,y,z":
            self.reset("Board startup detected; recalibrate the arm")
            return
        if line.startswith("# ERROR"):
            self.stop()
            self.note(line[2:])
            return
        for i, prefix in ((1, "# HAPTIC"), (2, "# HAPTIC2")):
            if line.startswith(prefix+" ON "):
                if self.motors[i]["pending"] is not None:
                    self.motors[i]["pending"] = None
                    self.motors[i]["acks"] += 1
                return
            if line == prefix+" OFF":
                if self.motors[i]["percent"] and now < self.motors[i]["until"]:
                    self.stop()
                    self.note("Motor test stopped: firmware pulse expired before renewal")
                return
        if line.startswith("# JOYSTICK,") and self.name == "wroom":
            try:
                _, stamp, x, y, pressed = line.split(",")
                stamp, x, y, pressed = map(int, (stamp, x, y, pressed))
                if not (0 <= stamp <= 0xffffffff and 0 <= x <= 4095 and 0 <= y <= 4095 and pressed in (0, 1)):
                    return
                if self.joystick and stamp < self.joystick["uptime_ms"]:
                    self.reset("WROOM restarted; recalibrate the arm")
                self.joystick = dict(x=x, y=y, pressed=pressed, uptime_ms=stamp, seen=now)
            except (ValueError, TypeError):
                pass
            return
        if line.startswith("#"):
            return
        try:
            parts = line.split(",")
            if self.name == "wroom" and len(parts) == 4:
                sensor = "wroom"
            elif self.name == "s3" and len(parts) == 5 and parts[0] in ("imu1", "imu2"):
                sensor = "s3_"+parts.pop(0)
            else:
                return
            q = normalize(tuple(map(float, parts)))
            sample = self.samples[sensor]
            if sample["q"] is not None and now-sample["seen"] > 1:
                self.stop()
                self.note("IMU stream resumed after a gap; motors stopped")
            sample.update(q=q, seen=now, sequence=sample["sequence"]+1)
            sample["times"].append(now)
        except ValueError:
            pass

    def snapshot(self):
        return deepcopy({"port": self.port, "connected": self.connected, "epoch": self.epoch,"reset_reason":self.reset_reason,
            "fields": self.fields, "status_at": self.status_at, "message": self.message,
            "log": list(self.log), "joystick": self.joystick, "samples": self.samples, "motors": self.motors})


class WearableHub:
    demo = False

    def __init__(self, wroom="COM7", s3="COM8"):
        if wroom.upper() == s3.upper():
            raise ValueError("WROOM and S3 must use different COM ports")
        self.boards = {"wroom": Board("wroom", wroom), "s3": Board("s3", s3)}
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.threads = []

    def start(self):
        for board in self.boards.values():
            thread = threading.Thread(target=self.worker, args=(board,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def worker(self, board):
        import serial
        while not self.closed.is_set():
            connection = None
            try:
                connection = serial.Serial(port=None, baudrate=115200, timeout=.02, write_timeout=.3)
                connection.dtr = connection.rts = False
                connection.port = board.port
                connection.open()
                connection.reset_input_buffer()
                with self.lock:
                    board.connected = True
                    board.reset("Connected; reading device status")
                buffer = b""
                while not self.closed.is_set():
                    with self.lock:
                        commands = board.commands(time.monotonic())
                    # Serial writes can wait for the OS; never hold the GUI's
                    # snapshot lock while performing serial I/O.
                    for command in commands:
                        connection.write((command+"\n").encode("ascii"))
                    data = connection.read(min(connection.in_waiting or 1, 4096))
                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        with self.lock:
                            board.ingest(line.decode("ascii", errors="ignore").strip(), time.monotonic())
                    if len(buffer) > 4096:
                        buffer = b""
            except (serial.SerialException, OSError) as error:
                with self.lock:
                    board.connected = False
                    missing = "FileNotFoundError" in str(error) or getattr(error, "errno", None) == 2
                    hint = "Port not present. Check the USB cable and COM port number." if missing else "Close dashboard/robot/Arduino Serial Monitor if the port is busy."
                    board.reset(f"{board.port}: {hint} ({error})")
            finally:
                if connection is not None and connection.is_open:
                    try:
                        connection.write(b"STOP\n")
                        connection.flush()
                    except (serial.SerialException, OSError):
                        pass
                    connection.close()
                with self.lock:
                    board.connected = False
            self.closed.wait(2)

    def snapshot(self):
        with self.lock:
            return {name: board.snapshot() for name, board in self.boards.items()}

    def motor(self, board, motor, percent, seconds):
        with self.lock:
            self.boards[board].start(motor, percent, seconds, time.monotonic())

    def intensity(self, board, motor, percent):
        with self.lock:
            self.boards[board].set_intensity(motor, percent)

    def stop(self, board=None, motor=None):
        with self.lock:
            for name, state in self.boards.items():
                if board is None or board == name:
                    state.stop(motor)

    def close(self):
        self.stop()
        self.closed.set()
        for thread in self.threads:
            thread.join(timeout=1)


def fresh_frame(snapshot, now):
    samples = {key: sample for board in snapshot.values() for key, sample in board["samples"].items()}
    for key, sample in samples.items():
        if sample["q"] is None or not 0 <= now-sample["seen"] < .4:
            raise ValueError(f"{key}: waiting for fresh IMU readings")
    times = [sample["seen"] for sample in samples.values()]
    if max(times)-min(times) > .15:
        raise ValueError("IMU readings are out of sync; wait for the streams to recover")
    return {key: s["q"] for key, s in samples.items()}, {key: s["sequence"] for key, s in samples.items()}


def axis_q(axis, radians):
    v = [0., 0., 0.]
    v[axis] = math.sin(radians/2)
    return (math.cos(radians/2), *v)


class DemoHub(WearableHub):
    demo = True

    def __init__(self):
        super().__init__("DEMO WROOM", "DEMO S3")
        self.pose = None
        self.started = time.monotonic()
        self.mounts = {"wroom": axis_q(0, .6), "s3_imu1": axis_q(2, .9), "s3_imu2": axis_q(1, -.4)}
        self.headings = {"wroom": axis_q(2, 1.2), "s3_imu1": axis_q(2, -.8), "s3_imu2": axis_q(2, .3)}
        for board in self.boards.values():
            board.connected = True
            board.fields = {"DRV2605L": "1", "PWM2": "1", "JOYSTICK": "1"}
            board.note("DEMO: synthetic readings; no hardware connected")

    def start(self): pass

    def frame(self, pose, t=0):
        angles = {"s3_imu1": .25+.2*math.sin(t), "s3_imu2": -.6+.5*math.sin(t), "wroom": -.5+.6*math.sin(t)}
        frame = {}
        for key,mount in self.mounts.items():
            rotation = axis_q(1,0 if pose == "A" else math.pi/2 if pose == "B" else angles[key])
            if pose is None:
                rotation = multiply(axis_q(2,.6*math.sin(t*.6)),rotation)
                if key == "wroom": rotation = multiply(rotation,axis_q(0,.4*math.sin(t*.9)))
            frame[key] = multiply(multiply(self.headings[key],rotation),mount)
        return frame

    def snapshot(self):
        now = time.monotonic()
        t = now-self.started
        frame = self.frame(self.pose, t)
        for board in self.boards.values():
            board.status_at = now
            for key, sample in board.samples.items():
                sample.update(q=frame[key], seen=now, sequence=sample["sequence"]+1)
                sample["times"].append(now)
            for i, motor in board.motors.items():
                if now >= motor["until"]:
                    board.stop(i)
                elif motor["percent"]:
                    motor.update(running=True, acks=motor["acks"]+1)
        self.boards["wroom"].joystick = dict(x=round(2048+1400*math.sin(t)), y=round(2048+1400*math.cos(t)), pressed=int(int(t)%6==0), seen=now, uptime_ms=int(t*1000))
        return super().snapshot()
