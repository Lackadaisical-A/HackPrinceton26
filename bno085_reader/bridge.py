# /// script
# requires-python = ">=3.10"
# dependencies = ["pyserial>=3.5", "firebase-admin>=6"]
# ///
"""Publish serial quaternions to Firebase; optionally record raw samples."""
import argparse
import json
import math
import pathlib
import threading
import time
import uuid
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, db
import serial

ROOT = pathlib.Path(__file__).resolve().parents[1]


def parse_sample(line, prefix):
    parts = line.split(",")
    sensor_id = prefix
    if len(parts) == 5:
        label = parts.pop(0)
        if not label or any(c in label for c in '.#$[]/'):
            raise ValueError("Invalid sensor ID")
        sensor_id = prefix + "_" + label
    if len(parts) != 4:
        raise ValueError("Expected quaternion")
    q = [float(v) for v in parts]
    if not all(math.isfinite(v) for v in q):
        raise ValueError("Nonfinite quaternion")
    norm = math.sqrt(sum(v*v for v in q))
    if norm < 1e-6:
        raise ValueError("Zero quaternion")
    return sensor_id, q, norm


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="COM7")
    parser.add_argument("--sensor", default="arm")
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--test", action="store_true", help="Write/read/delete an isolated test entry; no serial port needed")
    args = parser.parse_args()
    if not args.sensor or any(c in args.sensor for c in '.#$[]/'):
        parser.error("Sensor name cannot contain . # $ [ ] /")
    config = ROOT / "config"
    keys = list(config.glob("*.json"))
    if len(keys) != 1:
        parser.error("Expected exactly one service-account JSON in config/")
    firebase_admin.initialize_app(credentials.Certificate(str(keys[0])), {
        "databaseURL": (config / "source.txt").read_text().strip(),
        "httpTimeout": 5,
    })
    if args.test:
        reference = db.reference("tests/" + uuid.uuid4().hex)
        try:
            reference.set({"message": "bridge connection test", "ok": True})
            assert reference.get()["ok"] is True
            print("PASS: Firebase write and read succeeded.")
        finally:
            reference.delete()
        print("Test entry deleted. Live sensor data was untouched.")
        return

    latest = {}
    latest_lock = threading.Lock()
    stop = threading.Event()
    reference = db.reference("live/sensors")

    def publish():
        while True:
            with latest_lock:
                batch = dict(latest)
                latest.clear()
            if not batch:
                if stop.is_set():
                    break
                stop.wait(.05)
                continue
            try:
                reference.update(batch)
            except Exception as error:
                print(f"Firebase upload failed ({type(error).__name__}); local recording continues.", flush=True)
            stop.wait(.05)

    worker = threading.Thread(target=publish, daemon=True)
    recording = None
    folder = None
    count = 0
    started = time.monotonic_ns()
    try:
        with serial.Serial(args.port, 115200, timeout=.3) as connection:
            if args.record:
                folder = ROOT / "recordings" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:6])
                folder.mkdir(parents=True)
                (folder / "metadata.json").write_text(json.dumps({
                    "schema_version": 2, "sensor_prefix": args.sensor, "port": args.port,
                    "serial_formats": ["w,x,y,z", "sensor_id,w,x,y,z"],
                    "started_at_utc": datetime.now(timezone.utc).isoformat(),
                    "quaternion_order": "wxyz", "report": "game_rotation_vector",
                    "requested_sensor_hz": 50, "timestamp_source": "host_serial_arrival",
                    "neutral_pose": None,
                }, indent=2))
                recording = (folder / "samples.jsonl").open("w", buffering=1)
                print(f"Recording: {folder}", flush=True)
            worker.start()
            print(f"Reading {args.port}; publishing /live/sensors/{args.sensor} (tagged sensors add _imu1/_imu2). Ctrl+C stops.", flush=True)
            last_log = 0
            while True:
                line = connection.readline().decode("ascii", errors="ignore").strip()
                try:
                    sensor_id, q, norm = parse_sample(line, args.sensor)
                except ValueError:
                    continue
                count += 1
                elapsed = time.monotonic_ns() - started
                raw = {"sensor_id": sensor_id, "sequence": count, "host_elapsed_ns": elapsed,
                       "timestamp_ms": time.time_ns() // 1_000_000, "quaternion_wxyz": q}
                if recording:
                    recording.write(json.dumps(raw) + "\n")
                sample = {"sensor_id": sensor_id, "sequence": count,
                          "timestamp_ms": raw["timestamp_ms"],
                          "quaternion": dict(zip(("w", "x", "y", "z"), (v/norm for v in q)))}
                with latest_lock:
                    latest[sensor_id] = sample
                if elapsed - last_log >= 1_000_000_000:
                    print(f"Received {count} samples; quaternion {q}", flush=True)
                    last_log = elapsed
    except KeyboardInterrupt:
        print("\nStopping…", flush=True)
    except serial.SerialException as error:
        print(f"Serial error: {error}. Disconnect the viewer and close Serial Monitor.", flush=True)
    finally:
        stop.set()
        if worker.is_alive():
            worker.join(timeout=7)
        if recording:
            recording.close()
            (folder / "outcome.json").write_text(json.dumps({"samples": count, "success": None, "notes": ""}, indent=2))
            print(f"Saved {count} samples in {folder}")


if __name__ == "__main__":
    main()
