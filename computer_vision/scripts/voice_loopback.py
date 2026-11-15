"""Loopback test of the stand-in recognizer behind `python -m paul_vision.voice --listen`.

Windows' own synthesizer speaks a list of phrases into wav files, the stand-in recognizer
(scripts/voice_listen.ps1, through paul_vision.voice.StandInRecognizer, the same code path as --listen) hears them
with the command grammar, and each recognition is parsed exactly as --listen would. Every phrase is scored against
what its intended text parses to:

    right          the command that would have been sent is the intended one (or nothing, for a negative phrase)
    missed         a command was intended and nothing would have been sent
    WRONG          a command would have been sent that was not intended: the number that matters

Everything stays on this computer: System.Speech synthesis and recognition, no network. This is synthetic speech,
not a microphone: it says whether the grammar and the confidence threshold behave, not how a room will sound.

    uv run python scripts/voice_loopback.py --work-dir <empty folder> [--snr-db 15] [--tag "my flask"]
"""
from __future__ import annotations

import argparse
import array
import json
import math
import queue
import random
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paul_vision.config import DEFAULT_CONFIG_PATH, load_config  # noqa: E402
from paul_vision.labels import library_dir, read_tag_names  # noqa: E402
from paul_vision.voice import (POWERSHELL, ParseRules, StandInRecognizer, listen_grammar,  # noqa: E402
                               parse_utterance)

VOICES = ("Microsoft David Desktop", "Microsoft Zira Desktop")

# (what is said, why). Commands: their intended parse is a command. Negatives: their intended parse sends nothing.
COMMANDS = (
    "paul find the cup", "hey paul find the pill bottle", "paul grab the bottle", "paul pick up the box",
    "paul look for the cup", "ok paul select the bottle", "paul track the pill bottle", "paul fetch my box",
    "paul find anything", "paul any object", "paul never mind", "paul cancel", "hey paul clear the target",
    "paul phase search", "paul phase approach", "paul set phase to grasp", "paul carry phase", "paul phase place",
    "paul phase release", "okay paul set the phase to search",
)
TAG_COMMANDS = ("paul find {tag}", "paul forget {tag}")
NAMING = ("paul this is my flask", "paul call this the red mug", "paul remember this as water bottle",
          "paul learn phone", "paul also look for green apple", "hey paul this is the blue box")
NEGATIVES = (
    "find the cup", "phase release", "grab the bottle",                                # no wake word
    "paul find the banana", "paul find my keys", "paul find the cap", "paul find the bowl",  # not a label
    "paul phase dance", "paul set phase to lunch",                                      # not a phase
    "what time is it", "the cup is on the table", "could you pass me the bottle", "paul how are you",
    "I put the box over there", "that is a nice bottle", "paula find the cup", "call me later",
)

SYNTH = r"""
param([string]$Jobs)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($job in (Get-Content -LiteralPath $Jobs -Raw -Encoding UTF8 | ConvertFrom-Json)) {
    $s.SelectVoice($job.voice)
    $s.Rate = [int]$job.rate
    $s.SetOutputToWaveFile($job.path, $fmt)
    $s.Speak($job.text)
    $s.SetOutputToNull()
}
$s.Dispose()
"""


def synthesize(jobs: list[dict], work: Path) -> None:
    script, listing = work / "synth.ps1", work / "synth_jobs.json"
    script.write_text(SYNTH, encoding="utf-8")
    listing.write_text(json.dumps(jobs), encoding="utf-8")
    subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-Jobs", str(listing)], check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def pad_and_noise(path: Path, snr_db: float | None, rng: random.Random, lead_s: float = 0.4,
                  tail_s: float = 0.6) -> None:
    """Silence around the phrase (as a microphone hears it), and white noise at snr_db below the speech, if given."""
    with wave.open(str(path), "rb") as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    speech = array.array("h", frames)
    rate = params.framerate
    samples = array.array("h", [0] * int(lead_s * rate)) + speech + array.array("h", [0] * int(tail_s * rate))
    if snr_db is not None:
        voiced = [s for s in speech if abs(s) > 300] or [1]
        rms = math.sqrt(sum(s * s for s in voiced) / len(voiced))
        sigma = rms / (10 ** (snr_db / 20))
        samples = array.array("h", (max(-32768, min(32767, int(s + rng.gauss(0.0, sigma)))) for s in samples))
    with wave.open(str(path), "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())


def without_t(payload: dict | None) -> dict | None:
    return None if payload is None else {k: v for k, v in payload.items() if k != "t"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--work-dir", help="where the wav files go (default: a new temporary folder)")
    parser.add_argument("--snr-db", type=float, default=None, help="add white noise this many dB below the speech")
    parser.add_argument("--rate", type=int, action="append", help="synthesizer speaking rate(s), -10..10 (default 0)")
    parser.add_argument("--tag", action="append", default=[], help="pretend this tag exists (adds tag phrases)")
    parser.add_argument("--decoy", choices=("on", "off"), default=None,
                        help="free dictation beside the commands (default: voice.listen.decoy)")
    parser.add_argument("--adaptation", choices=("on", "off"), default=None,
                        help="the recognizer adapting to the speaker (default: voice.listen.adaptation)")
    parser.add_argument("--names", choices=("on", "off"), default=None,
                        help="tag names by dictation (default: voice.listen.dictated_names)")
    parser.add_argument("--json", help="also write every result here")
    args = parser.parse_args()

    cfg = load_config(args.config)
    rules = ParseRules.from_config(cfg)
    classes = list(cfg.detector.classes)
    tags = list(dict.fromkeys([*read_tag_names(library_dir(cfg)), *args.tag]))
    min_conf = cfg.voice.listen.min_confidence
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="voice_loopback_"))
    work.mkdir(parents=True, exist_ok=True)

    names = cfg.voice.listen.dictated_names if args.names is None else args.names == "on"
    phrases = [*COMMANDS, *(p.format(tag=t) for t in tags for p in TAG_COMMANDS), *(NAMING if names else ()),
               *NEGATIVES]
    jobs = []
    for voice in VOICES:
        for rate in args.rate or [0]:
            for i, text in enumerate(phrases):
                path = work / f"{voice.split()[1].lower()}_r{rate:+d}_{i:02d}.wav"
                jobs.append({"text": text, "voice": voice, "rate": rate, "path": str(path)})
    started = time.monotonic()
    synthesize(jobs, work)
    rng = random.Random(7)
    for job in jobs:
        pad_and_noise(Path(job["path"]), args.snr_db, rng)
    print(f"synthesized {len(jobs)} wav files in {time.monotonic() - started:.1f} s "
          f"({'clean' if args.snr_db is None else f'white noise at {args.snr_db:g} dB SNR'}) into {work}")

    wave_list = work / "wave_list.txt"
    wave_list.write_text("\n".join(job["path"] for job in jobs) + "\n", encoding="utf-8")
    events: queue.Queue = queue.Queue()
    listen = cfg.voice.listen
    decoy = listen.decoy if args.decoy is None else args.decoy == "on"
    adaptation = listen.adaptation if args.adaptation is None else args.adaptation == "on"
    spec = listen_grammar(classes, tags, cfg.voice.wake_words, listen.culture, decoy=decoy, adaptation=adaptation,
                          dictated_names=names, decoy_words=listen.decoy_words)
    rec = StandInRecognizer(spec, events, wave_list=wave_list)
    rec.start()
    heard: dict[str, dict] = {}
    ready = None
    try:
        while True:
            kind, _proc, item = events.get(timeout=120)
            if kind == "recognizer_ended":
                if item["code"] != 0:
                    print(f"recognizer failed (exit {item['code']}): {item['stderr']}")
                    return 1
                break
            if "ready_ms" in item:
                ready = (1000 * (time.monotonic() - rec.started), item["ready_ms"])
            elif "wav" in item:
                heard[item["wav"]] = item
    finally:
        rec.stop()
    finished = time.monotonic() - rec.started
    print(f"recognizer ready {ready[0]:.0f} ms after start ({ready[1]} ms loading the grammar); "
          f"{len(jobs)} files recognized in {finished:.1f} s; min_confidence {min_conf:.2f}; "
          f"decoy {'on' if decoy else 'off'}; adaptation {'on' if adaptation else 'off'}; "
          f"dictated names {'on' if names else 'off'}; decoy words {', '.join(listen.decoy_words) or 'none'}")

    counts = {"right": 0, "missed": 0, "WRONG": 0}
    results = []
    print(f"\n{'voice':6} {'said':36} {'recognized (* = decoy, never sent)':38} {'conf':>5}  outcome")
    for job in jobs:
        item = heard.get(job["path"], {"text": None})
        intended = without_t(parse_utterance(job["text"], classes, tags, rules, 0.0).payload)
        text, conf = item.get("text"), float(item.get("confidence") or 0.0)
        is_decoy = item.get("grammar") == "decoy"
        sent = None
        if text and not is_decoy and conf >= min_conf:
            sent = without_t(parse_utterance(text, classes, tags, rules, 0.0).payload)
        outcome = "right" if sent == intended else ("missed" if sent is None else "WRONG")
        counts[outcome] += 1
        results.append({**job, "recognized": text, "confidence": conf if text else None, "decoy": is_decoy,
                        "intended": intended, "sent": sent, "outcome": outcome})
        shown = "-" if text is None else ("* " if is_decoy else "") + text
        print(f"{job['voice'].split()[1]:6} {job['text']:36} {shown[:38]:38} {conf if text else 0:5.2f}  {outcome}"
              + ("" if outcome == "right" else f"  (intended {intended}, would send {sent})"))

    n_cmd = sum(1 for r in results if r["intended"] is not None)
    print(f"\n{len(results)} phrases ({n_cmd} commands, {len(results) - n_cmd} negatives): "
          f"{counts['right']} right, {counts['missed']} missed, {counts['WRONG']} WRONG commands would have been sent")
    # What a different threshold would have done, from the same recognitions.
    for threshold in (0.5, 0.6, 0.7, 0.8, 0.9):
        wrong = missed = 0
        for r in results:
            sent = None
            if r["recognized"] and not r["decoy"] and r["confidence"] >= threshold:
                sent = without_t(parse_utterance(r["recognized"], classes, tags, rules, 0.0).payload)
            wrong += sent is not None and sent != r["intended"]
            missed += sent is None and r["intended"] is not None
        print(f"  at min_confidence {threshold:.1f}: {wrong} wrong, {missed} missed")
    if args.json:
        Path(args.json).write_text(json.dumps({"ready": ready, "counts": counts, "results": results}, indent=1),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
