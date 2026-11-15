# /// script
# requires-python = ">=3.10"
# dependencies = ["firebase-admin>=6"]
# ///
"""Upload a completed local session to /recordings/<session_id> in RTDB."""
import argparse
import json
import pathlib

import firebase_admin
from firebase_admin import credentials, db

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="Folder name inside recordings/")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.session or any(c in args.session for c in '.#$[]/\\'):
        parser.error("Use the session folder name only")
    folder = ROOT / "recordings" / args.session
    metadata = json.loads((folder / "metadata.json").read_text())
    outcome_path = folder / "outcome.json"
    if not outcome_path.exists():
        parser.error("Session has no outcome.json. Stop the recording before uploading.")
    outcome = json.loads(outcome_path.read_text())
    samples = {}
    with (folder / "samples.jsonl").open() as stream:
        for line in stream:
            if line.strip():
                sample = json.loads(line)
                samples[f"{len(samples):09d}"] = sample
    if not samples:
        parser.error("Recording contains no samples")
    if outcome.get("samples") != len(samples):
        parser.error("Sample count differs from outcome.json; recording may be incomplete")
    print(f"Session: {args.session}; samples: {len(samples)}")
    print(f"Destination: /recordings/{args.session}")
    if args.dry_run:
        print("Validated. No database changes made.")
        return
    config = ROOT / "config"
    keys = list(config.glob("*.json"))
    if len(keys) != 1:
        parser.error("Expected exactly one service-account JSON in config/")
    firebase_admin.initialize_app(credentials.Certificate(str(keys[0])), {
        "databaseURL": (config / "source.txt").read_text().strip(),
        "httpTimeout": 30,
    })
    ref = db.reference("recordings/" + args.session)
    if ref.child("status").get() == "complete":
        parser.error("Session is already uploaded; existing recording was left unchanged")
    ref.update({"status": "uploading", "metadata": metadata, "outcome": outcome})
    items = list(samples.items())
    for start in range(0, len(items), 250):
        ref.child("samples").update(dict(items[start:start + 250]))
        print(f"Uploaded {min(start + 250, len(items))}/{len(items)} samples", flush=True)
    ref.update({"status": "complete", "sample_count": len(samples),
                "uploaded_at_ms": {".sv": "timestamp"}})
    print("Upload complete. Readers should only replay sessions with status=complete.")


if __name__ == "__main__":
    main()
