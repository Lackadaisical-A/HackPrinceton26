"""Pure logic for one smart-replay turn: the prompt, the action schema, and cleaning Gemini's chosen action.

Gemini chooses; the robot process (robot_control/smart_replay.py) validates again and executes within its own
limits. No I/O here.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .task import describe_waypoints

TOPIC = "agent"
ACTIONS = ("look", "go_to_waypoint", "play_segment", "nudge", "move_joints", "grip", "ask_operator", "done")
DIRECTIONS = ("left", "right", "forward", "back", "up", "down")
ARM_JOINTS = ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")
MAX_HISTORY = 14
MAX_TEXT_CHARS = 300

AGENT_SYSTEM = """\
You control a real robot arm (an SO-101 with a two-finger gripper) to repeat a task you studied from one \
demonstration. You see through the camera on the arm's wrist, looking along the gripper. Each turn you get the \
current picture, the arm's state, and the result of your last action. You answer with exactly ONE action. \
The arm carries it out, then you are asked again. Objects may be in slightly different places than in the \
demonstration, and things can go wrong: check each result and picture, and correct course.

Actions:
- look: hold still and get a fresh picture. Optional "pan" (degrees, positive = turn left, at most 25) to look around.
- go_to_waypoint: move to the demonstration's pose at "waypoint". Does not open or close the gripper.
- play_segment: replay the demonstration from "waypoint" to the later waypoint "to" at its recorded speed. This \
reproduces the recorded gripper motion too, so a segment that contains a grasp or a release will grasp or release.
- nudge: move the gripper tip a short straight step. "direction" is left, right, forward (away from the arm's \
base), back, up or down, as the arm sees it; "amount" is centimetres, at most 5.
- move_joints: turn joints by small amounts. "joints" gives degrees per joint, at most 10 each. Use only when \
waypoints and nudges cannot do what is needed.
- grip: "grip" is "close" or "open". A close reports whether an object is now held.
- ask_operator: stop and ask the human for help; put the reason in "text". Use when you are stuck or unsure it is safe.
- done: the task is finished (or cannot be); put a one-sentence summary in "text".

How to work:
- Follow your brief step by step. Prefer play_segment for the long moves that still fit, go_to_waypoint to get \
into position, and nudge for small corrections. Raw joint moves are a last resort.
- Before closing the gripper, make sure the object is between the fingers in the picture. If it is off to one \
side, nudge first.
- After each action, compare the picture and the arm state with what the step expected. If a grasp closed on \
nothing, open, look, adjust and try again. If an object was dropped, find it and pick it up again. If a move \
stalled, back off to an earlier waypoint and try another way.
- If the same thing fails three times, ask the operator instead of repeating it.
- "holding" in the arm state is sensed from the gripper motor: trust it over the picture.
- Never try to move outside what the actions allow. Stop with done when the brief's "done_when" is true.
- "why" is one short sentence explaining this action. "step" is the number of the brief step you are working on.
"""

ACTION_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "enum": list(ACTIONS)},
        "why": {"type": "STRING", "description": "One short sentence."},
        "step": {"type": "INTEGER", "nullable": True, "description": "Brief step number (from 1) being worked on."},
        "waypoint": {"type": "INTEGER", "nullable": True},
        "to": {"type": "INTEGER", "nullable": True},
        "direction": {"type": "STRING", "enum": list(DIRECTIONS), "nullable": True},
        "amount": {"type": "NUMBER", "nullable": True, "description": "Centimetres for nudge."},
        "pan": {"type": "NUMBER", "nullable": True, "description": "Degrees for look."},
        "joints": {
            "type": "OBJECT", "nullable": True,
            "properties": {name: {"type": "NUMBER", "nullable": True} for name in ARM_JOINTS},
        },
        "grip": {"type": "STRING", "enum": ["open", "close"], "nullable": True},
        "text": {"type": "STRING", "nullable": True},
    },
    "required": ["action", "why"],
}


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_action(raw: Any) -> dict[str, Any]:
    """Gemini's answer reduced to the fields its action uses. Raises ValueError if there is no usable action.

    Ranges are not judged here: the robot clamps or refuses, and tells Gemini why in the next turn.
    """
    if not isinstance(raw, dict) or raw.get("action") not in ACTIONS:
        raise ValueError("the answer names no known action")
    kind = raw["action"]
    action: dict[str, Any] = {"action": kind, "why": _text(raw.get("why"), 200)}
    if type(raw.get("step")) is int:
        action["step"] = raw["step"]
    if kind in ("go_to_waypoint", "play_segment"):
        action["waypoint"] = raw.get("waypoint") if type(raw.get("waypoint")) is int else None
    if kind == "play_segment":
        action["to"] = raw.get("to") if type(raw.get("to")) is int else None
    if kind == "nudge":
        action["direction"] = raw.get("direction") if raw.get("direction") in DIRECTIONS else None
        action["amount"] = raw.get("amount") if _number(raw.get("amount")) else None
    if kind == "look" and _number(raw.get("pan")) and raw["pan"]:
        action["pan"] = raw["pan"]
    if kind == "move_joints":
        joints = raw.get("joints") if isinstance(raw.get("joints"), dict) else {}
        action["joints"] = {n: joints[n] for n in ARM_JOINTS if _number(joints.get(n)) and joints[n]}
    if kind == "grip":
        action["grip"] = raw.get("grip") if raw.get("grip") in ("open", "close") else None
    if kind in ("ask_operator", "done"):
        action["text"] = _text(raw.get("text")) or action["why"]
    return action


def describe_action(action: dict[str, Any]) -> str:
    """One line for the history: the action and its arguments, without the reason."""
    extras = {k: v for k, v in action.items() if k not in ("action", "why", "step") and v not in (None, {}, "")}
    return action["action"] + (" " + json.dumps(extras, separators=(",", ":")) if extras else "")


def history_entry(action: dict[str, Any], result: dict[str, Any] | None) -> str:
    outcome = "no result yet" if result is None else ("ok" if result.get("ok") else "FAILED") + ": " + _text(result.get("detail"))
    return f"{describe_action(action)} -> {outcome}"


def build_turn_prompt(brief: dict[str, Any], points: list[float], events: list[dict[str, Any]],
                      history: list[str], arm: dict[str, Any] | None, result: dict[str, Any] | None,
                      vision: dict[str, Any] | None, turn: int) -> str:
    """The text for one turn. Pictures are sent alongside, the current one last."""
    lines = ["Your brief:", json.dumps(brief, indent=1), "", "Waypoints of the demonstration:",
             describe_waypoints(points, events), ""]
    if history:
        lines += ["What you have done so far (oldest first):", *history[-MAX_HISTORY:], ""]
    if result:
        lines.append(("Result of your last action: ok. " if result.get("ok") else "Result of your last action: FAILED. ")
                     + _text(result.get("detail")))
    if arm:
        joints = ", ".join(f"{name} {value:g}" for name, value in (arm.get("joints") or {}).items())
        lines.append(f"Arm now: joints in degrees: {joints}; gripper {arm.get('gripper')}% open; "
                     f"holding an object: {'yes' if arm.get('holding') else 'no'}"
                     + ("; gripper is fully closed on nothing" if arm.get("closed_endpoint") else "") + ".")
    if vision:
        target = vision.get("target") if isinstance(vision.get("target"), dict) else None
        guidance = vision.get("guidance") if isinstance(vision.get("guidance"), dict) else None
        if target and guidance:
            lines.append(f"The object detector is locked on '{target.get('label')}': it is "
                         f"{guidance.get('dx', 0):+.2f} right and {guidance.get('dy', 0):+.2f} down of the gripper's "
                         f"grasp point in the picture (-1..1), distance {guidance.get('distance_hint')}.")
        if vision.get("alert") == "dropped":
            lines.append("The object detector reports that an object may have been dropped.")
    lines.append(f"This is turn {turn}. The last picture is what the camera sees now. Choose your one action.")
    return "\n".join(lines)


def build_agent_message(t: float, *, run: Any, seq: int | None, status: str, action: dict[str, Any] | None = None,
                        detail: str | None = None, model: str | None = None) -> dict[str, Any]:
    """One `agent` message. With an action it answers turn `seq` of `run`; without, it only reports status.

    The robot acts only on a message whose run and seq match the turn it is waiting on, so a status-only
    message carries seq None.
    """
    message: dict[str, Any] = {"t": round(t, 3), "run": run, "seq": seq if action else None, "turn": seq,
                               "status": status, "detail": detail, "model": model}
    if action:
        message.update(action)
    return message
