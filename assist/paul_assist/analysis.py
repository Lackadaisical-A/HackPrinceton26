"""Pure logic: the prompt, the answer schema, and turning Gemini's answer into an `assist` message.

No I/O here, so every rule can be tested without a camera, a network or an API key.
"""

from __future__ import annotations

import re
from typing import Any

TOPIC = "assist"
STATUSES = ("working", "ok", "error")
URGENCIES = ("none", "watch", "urgent")
# What the picture shows of the patient. A single picture cannot tell sleep from unconsciousness, so
# "eyes_closed" covers both, and a touch check is how the operator finds out which.
PATIENT_STATES = ("awake", "eyes_closed", "distressed", "unclear", "not_visible")
# Gentle touches the operator can make with the gripper. The pads and the wearable's haptics let the
# operator feel the contact, which is why these are worth suggesting at all.
TOUCH_KINDS = (
    "wellness_nudge",    # a still patient with eyes closed: a light nudge on the shoulder or forearm, watch for a response
    "attention_tap",     # an awake patient who has not noticed the robot (turned away, headphones, hard of hearing)
    "guided_handover",   # hold the item against the patient's hand; release only when the pads feel them take it
    "weight_check",      # lift a cup or bottle to feel whether it is empty before handing it over or to refill
    "move_within_reach", # slide an item the patient is straining toward closer to their hand
    "call_button",       # press a visible nurse-call button for a patient who cannot
    "comfort_adjust",    # pull a blanket back over, or steady something about to fall
)
WELLNESS_NUDGE_TEXT = "Nudge the patient gently to check for wellness"
MAX_TOUCH_CHECKS = 3
# A previous answer older than this says nothing about the current picture.
PREVIOUS_MAX_AGE_S = 180.0

MAX_ITEMS = 3
MAX_SUGGESTIONS = 4
MAX_OBSERVATIONS = 4
# Mirrors computer_vision config.yaml labels: tag_text prompts and names are capped there.
MAX_PROMPT_CHARS = 100
MAX_NAME_CHARS = 40
MAX_TEXT_CHARS = 240

SYSTEM_INSTRUCTION = """\
You are the scene assistant of PAUL, a tele-operated robot arm that hands objects to a patient who is \
isolated (for example in quarantine) while a remote operator drives the arm. You see one picture from \
the camera on the arm's wrist.

Rules:
- Describe only what is visible in the picture. Never guess at anything you cannot see.
- You are not a doctor. Never diagnose, name a condition, or give medical advice. You may note visible \
signs a caregiver would want to know about (for example: a person lying still, holding their chest, \
coughing, reaching for something, looking distressed), phrased as observations.
- If anything looks like it could be an emergency, set urgency to "urgent" and make the first \
suggestion "Alert the caregiver now". Use "watch" for things worth keeping an eye on.
- Suggestions are short actions for the operator (under 12 words), most useful first.
- Items are physical objects the patient most likely needs that the arm could fetch and hand over \
(for example: water bottle, cup, pill bottle, inhaler, glasses, phone, remote control, tissues). \
At most three, most important first. Prefer names from the list of names the detector already knows. \
Give each item a short visual description (colour, shape) to help a detector find it. Only include \
items that are small enough for a small robot gripper.
- If no person is visible, set patient.present to false, patient.state to "not_visible", and suggest what the operator could do next.
- patient.state: "awake" (eyes open, moving or sitting up), "eyes_closed" (lying still with eyes closed: one picture cannot tell sleep from unconsciousness), "distressed" (visible pain, struggling, fallen), "unclear".
- Touch checks are gentle touches the operator can make with the robot's padded gripper, which lets them feel the contact. Suggest only those that fit what you see, most useful first, at most three:
  - wellness_nudge: the patient is still with eyes closed, or slumped, or not reacting. Always suggest it for "eyes_closed". The text should say to nudge the shoulder or forearm gently and watch for a response.
  - attention_tap: the patient is awake but has not noticed the robot.
  - guided_handover: an item is about to be handed over to a patient with a weak or unsteady grip.
  - weight_check: a cup or bottle might be empty.
  - move_within_reach: the patient is reaching or straining toward an item.
  - call_button: a nurse-call button is visible and the patient may need help.
  - comfort_adjust: a blanket has slipped off, or something is about to fall.
  Touches are only ever light, on the shoulder, upper arm, forearm or hand. Never the head, face, neck or chest, and never near wounds, dressings, tubes or lines.
- If the previous analysis suggested a wellness nudge and the patient still looks the same (still, eyes closed, same position), set urgency to "urgent": they may not be responding.
"""

# Gemini's structured output (google-genai `response_schema`, an OpenAPI subset).
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "scene": {"type": "STRING", "description": "One sentence: what the camera sees."},
        "patient": {
            "type": "OBJECT",
            "properties": {
                "present": {"type": "BOOLEAN"},
                "observations": {"type": "ARRAY", "items": {"type": "STRING"}},
                "urgency": {"type": "STRING", "enum": list(URGENCIES)},
                "state": {"type": "STRING", "enum": list(PATIENT_STATES)},
            },
            "required": ["present", "observations", "urgency", "state"],
        },
        "touch_checks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": list(TOUCH_KINDS)},
                    "text": {"type": "STRING", "description": "The action for the operator, under 15 words."},
                    "reason": {"type": "STRING", "description": "What in the picture calls for it."},
                },
                "required": ["kind", "text", "reason"],
            },
        },
        "suggestions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "text": {"type": "STRING"},
                    "item": {"type": "STRING", "nullable": True,
                             "description": "The item this suggestion is about, by its name in items, or null."},
                },
                "required": ["text"],
            },
        },
        "items": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "name": {"type": "STRING", "description": "Short lowercase name, e.g. 'inhaler'."},
                    "prompt": {"type": "STRING", "description": "Short visual description for a detector."},
                    "reason": {"type": "STRING", "description": "Why the patient may need it."},
                    "visible": {"type": "BOOLEAN", "description": "Whether it is in this picture."},
                },
                "required": ["name", "prompt", "reason", "visible"],
            },
        },
    },
    "required": ["scene", "patient", "suggestions", "items", "touch_checks"],
}


def build_prompt(known_labels: list[str], vision: dict[str, Any] | None,
                 previous: dict[str, Any] | None = None, previous_age_s: float | None = None) -> str:
    """The text that goes with the picture: what the detector knows, what vision is doing now, and the
    previous answer if it is recent (so a nudge that got no response can be followed up)."""
    lines = [f"Names the detector already knows: {', '.join(known_labels) if known_labels else '(none)'}."]
    if vision:
        target = vision.get("target") or {}
        label = target.get("label") if isinstance(target, dict) else None
        lines.append(f"Vision status: {vision.get('status')}, task phase: {vision.get('phase')}"
                     + (f", locked on: {label}." if label else "."))
    if previous and previous_age_s is not None and previous_age_s <= PREVIOUS_MAX_AGE_S:
        patient = previous.get("patient") or {}
        kinds = [c.get("kind") for c in previous.get("touch_checks") or []]
        lines.append(f"Previous analysis {previous_age_s:.0f} s ago: patient state {patient.get('state', 'unknown')}, "
                     f"urgency {patient.get('urgency', 'none')}; touch checks suggested: "
                     f"{', '.join(k for k in kinds if k) or 'none'}.")
    lines.append("Analyse the picture and answer in the requested JSON format.")
    return "\n".join(lines)


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def normalize_name(value: Any) -> str:
    """A detector-friendly name: lowercase, letters, digits and single spaces."""
    name = re.sub(r"[^a-z0-9 ]+", " ", _text(value).lower())
    return re.sub(r"\s+", " ", name).strip()[:MAX_NAME_CHARS].strip()


def match_known(name: str, known: list[str]) -> str | None:
    """The known label `name` refers to (exact, or plural/singular), else None."""
    lookup = {k.lower(): k for k in known}
    for candidate in (name, name.rstrip("s"), name + "s"):
        if candidate in lookup:
            return lookup[candidate]
    return None


def normalize_result(raw: Any, known_labels: list[str]) -> dict[str, Any]:
    """Gemini's answer, cleaned and capped so the console and vision can trust every field.

    Raises ValueError if the answer is not the shape that was asked for.
    """
    if not isinstance(raw, dict):
        raise ValueError("the answer is not a JSON object")
    patient = raw.get("patient") if isinstance(raw.get("patient"), dict) else {}
    urgency = patient.get("urgency") if patient.get("urgency") in URGENCIES else "none"

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw.get("items") or []:
        if not isinstance(entry, dict):
            continue
        name = normalize_name(entry.get("name"))
        if not name:
            continue
        known = match_known(name, known_labels)
        name = known or name
        if name in seen:
            continue
        seen.add(name)
        items.append({
            "name": name,
            "prompt": _text(entry.get("prompt"), MAX_PROMPT_CHARS) or name,
            "reason": _text(entry.get("reason")),
            "visible": entry.get("visible") is True,
            "known": known is not None,
        })
        if len(items) == MAX_ITEMS:
            break

    item_names = {i["name"] for i in items}
    suggestions = []
    for entry in raw.get("suggestions") or []:
        if not isinstance(entry, dict) or not _text(entry.get("text")):
            continue
        item = normalize_name(entry.get("item")) if entry.get("item") else ""
        item = match_known(item, sorted(item_names)) or (item if item in item_names else None)
        suggestions.append({"text": _text(entry.get("text")), "item": item})
        if len(suggestions) == MAX_SUGGESTIONS:
            break

    observations = [_text(o) for o in patient.get("observations") or [] if _text(o)][:MAX_OBSERVATIONS]
    scene = _text(raw.get("scene"))
    if not scene:
        raise ValueError("the answer has no scene description")
    present = patient.get("present") is True
    state = patient.get("state") if patient.get("state") in PATIENT_STATES else "unclear"
    if not present:
        state = "not_visible"
    elif state == "not_visible":
        state = "unclear"
    touch_checks = normalize_touch_checks(raw.get("touch_checks"), present)
    if state == "eyes_closed":
        # Sleep and unconsciousness look the same in one picture: the operator always gets the nudge,
        # and it is never "no concern" until someone has seen a response.
        if not any(c["kind"] == "wellness_nudge" for c in touch_checks):
            touch_checks = [{"kind": "wellness_nudge", "text": WELLNESS_NUDGE_TEXT,
                             "reason": "Eyes closed and still: asleep or unresponsive cannot be told apart from the picture."},
                            *touch_checks][:MAX_TOUCH_CHECKS]
        if urgency == "none":
            urgency = "watch"
    return {
        "scene": scene,
        "patient": {"present": present, "observations": observations, "urgency": urgency, "state": state},
        "suggestions": suggestions,
        "items": items,
        "touch_checks": touch_checks,
    }


# Touches that need a patient in view; the others are about objects.
_PATIENT_TOUCHES = {"wellness_nudge", "attention_tap", "guided_handover", "move_within_reach", "comfort_adjust"}


def normalize_touch_checks(raw: Any, patient_present: bool) -> list[dict[str, str]]:
    """Known kinds only, one of each, at most MAX_TOUCH_CHECKS; none aimed at a patient who is not in view."""
    checks: list[dict[str, str]] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or entry.get("kind") not in TOUCH_KINDS:
            continue
        kind = entry["kind"]
        if any(c["kind"] == kind for c in checks) or (kind in _PATIENT_TOUCHES and not patient_present):
            continue
        text = _text(entry.get("text")) or (WELLNESS_NUDGE_TEXT if kind == "wellness_nudge" else "")
        if not text:
            continue
        checks.append({"kind": kind, "text": text, "reason": _text(entry.get("reason"))})
        if len(checks) == MAX_TOUCH_CHECKS:
            break
    # The nudge goes first whenever it is there: it is the one that can reveal an emergency.
    checks.sort(key=lambda c: c["kind"] != "wellness_nudge")
    return checks


def build_message(seq: int, t: float, status: str, *, detail: str | None = None,
                  result: dict[str, Any] | None = None, model: str | None = None) -> dict[str, Any]:
    """One `assist` message. `result` only with status "ok"."""
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    if (status == "ok") != (result is not None):
        raise ValueError("result is required with status ok and not allowed otherwise")
    return {"t": round(t, 3), "seq": seq, "status": status, "detail": detail, "model": model, "result": result}
