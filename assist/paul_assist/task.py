"""Pure logic for studying a recorded demonstration: which pictures to show, the prompt, and the brief.

The brief is Gemini's own account of the task: what was done, the rule behind it, and ordered steps tied to
the recording's waypoints. No I/O here.
"""

from __future__ import annotations

import re
from typing import Any

TOPIC = "task"
STATUSES = ("working", "ok", "error")
MAX_KEYFRAMES = 12
MAX_STEPS = 12
MAX_OBJECTS = 6
MAX_TEXT_CHARS = 240

BRIEF_SYSTEM = """\
You are studying one demonstration of a tele-operated robot arm (an SO-101 with a two-finger gripper) so that \
you can repeat the task yourself later. The pictures come from the camera on the arm's wrist, looking along \
the gripper, in time order. Each picture is labelled with a waypoint number: a pose on the recording that you \
will be able to return to. Some waypoints are marked "grasp" (the gripper closed on something) or "release" \
(it let go).

Work out what the operator was doing and why, then write a brief you can follow later when objects may be in \
slightly different places.

Rules:
- Describe only what the pictures and the grasp/release marks support. If something is unclear, say so in the summary.
- "rule" is what generalises, in one sentence (for example: "white bottles go in the left bin, blue in the right").
- "done_when" is how you will know the task is finished, judged from a camera picture.
- "steps" are in order. Each covers a range of waypoints (from <= to) and has an "expect": what should be true, \
and visible or sensed, once the step is done (for example "gripper is above the white bottle", "bottle is held", \
"bottle is in the left bin and the gripper is open").
- "objects" are the things handled or used as destinations, each with a short visual description.
"""

BRIEF_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING", "description": "One sentence: what the demonstration does."},
        "rule": {"type": "STRING"},
        "done_when": {"type": "STRING"},
        "objects": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"name": {"type": "STRING"}, "description": {"type": "STRING"}},
                "required": ["name", "description"],
            },
        },
        "steps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING", "description": "What to do, in a few words."},
                    "from": {"type": "INTEGER", "description": "First waypoint of this step."},
                    "to": {"type": "INTEGER", "description": "Last waypoint of this step."},
                    "expect": {"type": "STRING"},
                },
                "required": ["text", "from", "to", "expect"],
            },
        },
    },
    "required": ["summary", "rule", "done_when", "objects", "steps"],
}


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def waypoint_marks(points: list[float], events: list[dict[str, Any]]) -> dict[int, str]:
    """Waypoint index -> "grasp" or "release", for the waypoints that sit on an event."""
    marks: dict[int, str] = {}
    for event in events:
        if not points:
            break
        index = min(range(len(points)), key=lambda i: abs(points[i] - event["t"]))
        if abs(points[index] - event["t"]) <= 0.05:
            marks[index] = event["kind"]
    return marks


def pick_keyframes(points: list[float], events: list[dict[str, Any]], limit: int = MAX_KEYFRAMES) -> list[int]:
    """The waypoints worth a picture: first, last, every grasp and release, then an even spread."""
    if not points:
        return []
    last = len(points) - 1
    marks = waypoint_marks(points, events)
    # Just before a grasp the object is still in view; at the grasp the gripper hides it.
    chosen = {0, last, *marks, *(i - 1 for i, kind in marks.items() if kind == "grasp" and i > 0)}
    for i in range(limit):
        if len(chosen) >= limit:
            break
        chosen.add(round(i * last / max(1, limit - 1)))
    ordered = sorted(chosen)
    if len(ordered) > 2 * limit:  # A take with very many grasps: thin it evenly rather than send every picture.
        ordered = sorted({ordered[round(i * (len(ordered) - 1) / (2 * limit - 1))] for i in range(2 * limit)})
    return ordered


def describe_waypoints(points: list[float], events: list[dict[str, Any]]) -> str:
    marks = waypoint_marks(points, events)
    return "\n".join(f"waypoint {i}: {t:.1f} s" + (f" ({marks[i]})" if i in marks else "") for i, t in enumerate(points))


def build_brief_prompt(points: list[float], events: list[dict[str, Any]]) -> str:
    return (f"The recording lasts {points[-1]:.1f} seconds and has {len(points)} waypoints:\n"
            f"{describe_waypoints(points, events)}\n\n"
            "Study the pictures above and answer in the requested JSON format.")


def normalize_brief(raw: Any, waypoint_count: int) -> dict[str, Any]:
    """Gemini's brief, cleaned and capped. Raises ValueError if it is not the shape that was asked for."""
    if not isinstance(raw, dict):
        raise ValueError("the brief is not a JSON object")
    summary = _text(raw.get("summary"))
    if not summary:
        raise ValueError("the brief has no summary")
    last = waypoint_count - 1
    steps = []
    for entry in raw.get("steps") or []:
        if not isinstance(entry, dict) or not _text(entry.get("text")):
            continue
        first, final = entry.get("from"), entry.get("to")
        if type(first) is not int or type(final) is not int:
            continue
        first, final = max(0, min(last, first)), max(0, min(last, final))
        steps.append({"text": _text(entry.get("text")), "from": min(first, final), "to": max(first, final),
                      "expect": _text(entry.get("expect"))})
        if len(steps) == MAX_STEPS:
            break
    if not steps:
        raise ValueError("the brief has no usable steps")
    objects = [{"name": _text(o.get("name"), 40), "description": _text(o.get("description"))}
               for o in raw.get("objects") or [] if isinstance(o, dict) and _text(o.get("name"), 40)][:MAX_OBJECTS]
    return {"summary": summary, "rule": _text(raw.get("rule")), "done_when": _text(raw.get("done_when")),
            "objects": objects, "steps": steps}


def build_task_message(seq: int, t: float, status: str, *, recording: float | None, detail: str | None = None,
                       brief: dict[str, Any] | None = None, model: str | None = None) -> dict[str, Any]:
    """One `task` message. `recording` names the take (its wall-clock start); `brief` only with status "ok"."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if (status == "ok") != (brief is not None):
        raise ValueError("brief is required with status ok and not allowed otherwise")
    return {"t": round(t, 3), "seq": seq, "status": status, "detail": detail, "model": model,
            "recording": recording, "brief": brief}
