"""The voice process (M5): utterances as text in, `command` messages to vision out.

    uv run python -m paul_vision.voice                         # one utterance per line on stdin (pipe a recognizer in)
    uv run python -m paul_vision.voice --no-wake-word          # typing at the keyboard: every line counts
    uv run python -m paul_vision.voice --say "find the cup"    # one utterance, then exit (0 = sent)
    uv run python -m paul_vision.voice --listen                # the local stand-in recognizer (Windows, see below)
    uv run python -m paul_vision.voice --grammar               # print every phrase it understands

It binds voice.endpoint (one of transport.command_sub) and vision connects to it. For every utterance it prints what
it understood and what it sent, or why it sent nothing. It never guesses: a label that is unknown, or too close to
two known labels, sends nothing. Known labels are detector.classes plus the operator's tags (the label library),
re-read for every utterance. Commands said before vision has subscribed are dropped, never queued, and the output
says so. Feedback is printed text only: this process plays no audio, never opens the servo bus, and sends nothing
anywhere but to vision on this computer.

--listen runs Windows' own speech recognizer (System.Speech, through Windows PowerShell 5.1, scripts/voice_listen.ps1)
with a grammar made of the command phrases and the known labels. It is a stand-in for the team's recognizer, so that
the demo can be driven by voice without a network. By default tag names are not dictated to it, only typed (see
`listen_grammar` and voice.listen in config.yaml). scripts/voice_loopback.py measures it without a microphone.
"""
from __future__ import annotations

import argparse
import dataclasses
import difflib
import json
import queue
import re
import subprocess
import sys
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import zmq

from . import clock
from .commands import CLEAR_TARGET, SELECT_TARGET, SET_PHASE, TAG_TEXT, TAG_VISUAL, UNTAG
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config
from .labels import LIBRARY_FILE, library_dir, name_problem, normalize_name, read_tag_names
from .messages import TOPIC_COMMAND, encode
from .phase import PHASES

# aruco.STATION_PICK and STATION_DROP: names a tag cannot take. aruco imports OpenCV, which this process must not.
STATION_NAMES = ("pick station", "drop station")

# ------------------------------------------------------------------------------------------------- the grammar
# Verb phrases, by the command they start. The longest phrase that begins the utterance wins ("this is called x"
# before "this is x"); no phrase belongs to two commands. A bare label or phase word is not a command.
VERBS: dict[str, tuple[str, ...]] = {
    SELECT_TARGET: ("find", "select", "look for", "search for", "target", "track", "go for", "pick up", "grab",
                    "get", "fetch"),
    TAG_VISUAL: ("this is", "this is called", "this one is", "this one is called", "call this", "call this one",
                 "call it", "name this", "name it", "remember this as", "remember this is", "remember it as",
                 "label this", "label this as", "learn this as"),
    TAG_TEXT: ("learn", "learn to find", "learn to recognize", "also look for", "also find", "also search for",
               "also track"),
    UNTAG: ("forget", "forget about", "unlearn", "remove tag", "remove the tag", "delete tag", "delete the tag"),
}
# The whole utterance, nothing more: any object can be the target again.
CLEAR_PHRASES = ("any object", "any objects", "anything", "any target", "clear", "clear target", "clear the target",
                 "never mind", "nevermind", "cancel", "reset target", "reset the target", "no target")
# "find anything" is clear_target too.
ANY_OBJECT = ("any", "any object", "any objects", "anything", "any target", "whatever")
# Phases need the word "phase", so a tag named like a phase never collides with set_phase.
_PHASE_PATTERNS = (
    re.compile(r"^(?:(?:set|change|switch)\s+)?(?:the\s+)?phase\s+(?:(?:to|is)\s+)?(?P<p>.+)$"),
    re.compile(r"^(?:(?:set|change|switch|go)\s+(?:the\s+)?(?:phase\s+)?(?:(?:to|into)\s+)?)?(?:the\s+)?"
               r"(?P<p>.+?)\s+phase$"),
)
PHASE_EXAMPLES = ("phase <phase>", "set phase to <phase>", "set the phase to <phase>", "<phase> phase",
                  "switch to <phase> phase")
# Dropped from the front of a label when matching ("find my cup" is the cup) and from tag names (a, an, the only:
# "this is my flask" names "my flask").
DETERMINERS = ("the", "a", "an", "my", "our", "your", "this", "that", "some")
ARTICLES = ("the", "a", "an")
# A tag name cannot be one of these, or start with a pointer word: "this is it" names nothing.
POINTER_WORDS = ("this", "that", "these", "those", "it", "them", "here", "there")
NOT_NAMES = ("one", "this one", "that one", "something", "nothing", "everything", *ANY_OBJECT)
_LEADING_FILLERS = ("please",)
_TRAILING_FILLERS = ("please", "thanks", "thank you")
EXAMPLES = "find <label>, any object, phase <phase>, this is <name>, learn <name>, forget <name>"

_PUNCTUATION = re.compile(r"[,.!?;:\"“”„«»()\[\]{}<>]")
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'"})


def words_of(text: str) -> list[str]:
    """Lower-case words: Unicode-normalized, sentence punctuation removed. `/` and `#` stay, so that a name holding
    them is refused rather than silently changed."""
    s = unicodedata.normalize("NFKC", str(text)).lower().translate(_APOSTROPHES)
    words = (w.strip("'-") for w in _PUNCTUATION.sub(" ", s).split())
    return [w for w in words if w]


@dataclass(frozen=True)
class ParseRules:
    """What the parser needs from the config. Tests build their own."""
    wake_words: tuple[str, ...]      # () = no wake word needed
    label_cutoff: float              # voice.label_cutoff
    label_margin: float              # voice.label_margin
    max_utterance_chars: int         # voice.max_utterance_chars
    max_name_chars: int              # labels.max_name_chars: the label library's own limit

    @classmethod
    def from_config(cls, cfg: Config) -> ParseRules:
        v = cfg.voice
        return cls(tuple(v.wake_words), v.label_cutoff, v.label_margin, v.max_utterance_chars,
                   cfg.labels.max_name_chars)


@dataclass(frozen=True)
class Parse:
    addressed: bool          # False: it did not start with a wake word, so it was not meant for PAUL
    payload: dict | None     # the command to send; None = send nothing
    message: str             # what was understood, or why nothing is sent


@dataclass(frozen=True)
class LabelMatch:
    label: str | None        # one of the known labels, spelled as it is known; None = no match
    note: str                # how the words were matched ("" = exactly), or why nothing matched


def _refuse(message: str) -> Parse:
    return Parse(True, None, message)


def strip_wake_word(words: Sequence[str], wake_words: Iterable[str]) -> list[str] | None:
    """The words after the longest wake word they begin with; None if they begin with none."""
    for wake in sorted((words_of(w) for w in wake_words), key=len, reverse=True):
        if wake and list(words[:len(wake)]) == wake:
            return list(words[len(wake):])
    return None


def is_addressed(text: str, rules: ParseRules, wake_word_required: bool = True) -> bool:
    """Whether the utterance is meant for PAUL: it starts with a wake word, or none is needed."""
    return not (rules.wake_words and wake_word_required) or strip_wake_word(words_of(text), rules.wake_words) is not None


def known_labels(classes: Sequence[str], tags: Sequence[str]) -> list[str]:
    """detector.classes, then the tags that are not among them (as labels.known_labels)."""
    return list(classes) + [t for t in tags if t not in classes]


def _strip_leading(words: Sequence[str], drop: Sequence[str]) -> list[str]:
    words = list(words)
    while words and words[0] in drop:
        words = words[1:]
    return words


def _strip_fillers(words: list[str]) -> list[str]:
    words = _strip_leading(words, _LEADING_FILLERS)
    changed = True
    while changed and words:
        changed = False
        for filler in _TRAILING_FILLERS:
            tail = filler.split()
            if len(words) > len(tail) and words[-len(tail):] == tail:
                words, changed = words[:-len(tail)], True
    return words


def _singulars(word: str) -> list[str]:
    out = []
    if word.endswith("es") and not word.endswith("ses") and len(word) > 3:
        out.append(word[:-2])                      # boxes -> box
    if word.endswith("s") and not word.endswith("ss") and len(word) > 2:
        out.append(word[:-1])                      # cups -> cup, bottles -> bottle
    return out


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


_NUMBERS = re.compile(r"\d+")


def spoken_form(label: str) -> str:
    """A label as it can be said: its words (words_of), so "mug (red)" is said "mug red". "" = it cannot be said."""
    return " ".join(words_of(label))


def _same_word(said: str, word: str, cutoff: float) -> bool:
    """Whether `said` can be `word` misheard: equal, a plain plural of it or the other way round, or, when both are
    longer than one character, at least `cutoff` similar."""
    if said == word or said in _singulars(word) or word in _singulars(said):
        return True
    return len(said) > 1 and len(word) > 1 and _ratio(said, word) >= cutoff


def _not_misheard(said: str, name: str, cutoff: float) -> str | None:
    """Why `said` cannot be `name` misheard, however similar the letters; None if it can be. The numbers must be the
    same ("bin 2" is not "bin 1"); with as many words on both sides, each word must pass `_same_word` ("red jug" is
    not "red mug"); otherwise a single letter or digit must be in both ("shelf b" is not "shelf")."""
    if _NUMBERS.findall(said) != _NUMBERS.findall(name):
        return "the numbers differ"
    said_words, name_words = said.split(), name.split()
    if len(said_words) == len(name_words):
        for a, b in zip(said_words, name_words):
            if not _same_word(a, b, cutoff):
                return f"'{a}' is not '{b}'"
        return None
    for a in [*said_words, *name_words]:
        if len(a) == 1 and not (a in said_words and a in name_words):
            return f"'{a}' must be said exactly"
    return None


def match_label(words: Sequence[str], known: Sequence[str], cutoff: float, margin: float,
                what: str = "label") -> LabelMatch:
    """The one known label the words name, or None with the reason. Never guesses.

    Labels are compared as they are said (`spoken_form`). Exact names win, in this order: the words as said, without
    leading determiners ("the cup"), then with a plain plural made singular ("cups"). Two labels said the same way
    ("mug (red)" and "mug red") are refused, not chosen between. Otherwise difflib similarity, only against labels the
    words could be misheard from (`_not_misheard`): the best must score at least `cutoff` and lead the runner-up by
    `margin`. A refusal names the closest label and its score.
    """
    spoken = " ".join(words)
    by_name: dict[str, list[str]] = {}  # spoken form -> the labels said that way (differing in more than case)
    for label in known:
        same = by_name.setdefault(spoken_form(label), [])
        if all(normalize_name(other) != normalize_name(label) for other in same):
            same.append(label)
    by_name.pop("", None)  # nothing can be said to name it
    if not words:
        return LabelMatch(None, f"no {what} given")
    if not by_name:
        return LabelMatch(None, f"no {what}s are known")
    stripped = _strip_leading(words, DETERMINERS)
    singular = [" ".join([*w[:-1], s]) for w in (list(words), stripped) if w for s in _singulars(w[-1])]
    tiers = [[spoken], [" ".join(stripped)] if stripped else [], singular]
    for tier, candidates in enumerate(tiers):
        names = {c for c in candidates if c in by_name}
        hits = sorted({label for c in names for label in by_name[c]})
        if len(hits) == 1:
            return LabelMatch(hits[0], "" if tier < 2 else f"'{spoken}' taken as '{hits[0]}'")
        if len(names) == 1:
            return LabelMatch(None, f"'{spoken}' could be any of {', '.join(hits)}, which are said the same way; "
                                    "not guessing")
        if len(hits) > 1:
            return LabelMatch(None, f"'{spoken}' could be any of {', '.join(hits)}; say which")
    candidates = [c for tier in tiers for c in tier]
    closest, allowed = [], []  # (score, label, why not) over every candidate; (score, label) over the allowed ones
    for name, labels in by_name.items():
        score, said = max((_ratio(c, name), c) for c in candidates)
        why = _not_misheard(said, name, cutoff)
        ok = [_ratio(c, name) for c in candidates if _not_misheard(c, name, cutoff) is None]
        for label in labels:
            closest.append((score, label, why))
            if ok:
                allowed.append((max(ok), label))
    closest.sort(key=lambda s: (-s[0], s[1]))
    allowed.sort(key=lambda s: (-s[0], s[1]))
    if not allowed or allowed[0][0] < cutoff:
        score, label, why = closest[0]
        reason = f"needs {cutoff:.2f}" if score < cutoff or why is None else f"but {why}"
        return LabelMatch(None, f"no known {what} like '{spoken}' (closest '{label}' scores {score:.2f}, {reason}); "
                                f"known: {', '.join(label for labels in by_name.values() for label in labels)}")
    best_score, best = allowed[0]
    if len(allowed) > 1 and best_score - allowed[1][0] < margin:
        second_score, second = allowed[1]
        return LabelMatch(None, f"'{spoken}' could be '{best}' ({best_score:.2f}) or '{second}' ({second_score:.2f}); "
                                "not guessing, say it exactly")
    return LabelMatch(best, f"heard '{spoken}' as '{best}', similarity {best_score:.2f}")


def _verb(words: Sequence[str]) -> tuple[str, list[str]] | None:
    """(command type, the words after the verb) for the longest verb phrase the words start with."""
    best: tuple[int, str] | None = None
    for kind, phrases in VERBS.items():
        for phrase in phrases:
            n = len(phrase.split())
            if list(words[:n]) == phrase.split() and (best is None or n > best[0]):
                best = (n, kind)
    return None if best is None else (best[1], list(words[best[0]:]))


def _with_note(text: str, note: str) -> str:
    return f"{text} ({note})" if note else text


def _select(rest: list[str], classes: Sequence[str], tags: Sequence[str], rules: ParseRules, t: float) -> Parse:
    core = _strip_leading(rest, DETERMINERS)
    if not core:
        return _refuse(f"select what? Say a label: {', '.join(known_labels(classes, tags))}")
    if " ".join(core) in ANY_OBJECT or " ".join(rest) in ANY_OBJECT:
        return _clear(t)
    m = match_label(rest, known_labels(classes, tags), rules.label_cutoff, rules.label_margin)
    if m.label is None:
        return _refuse(m.note)
    kind = "" if m.label in classes else "a tag"
    note = "; ".join(n for n in (m.note, kind) if n)
    return Parse(True, {"type": SELECT_TARGET, "label": m.label, "t": t}, _with_note(f"select target '{m.label}'", note))


def _clear(t: float) -> Parse:
    return Parse(True, {"type": CLEAR_TARGET, "t": t}, "clear target: any object can be the target again")


def _tag_name(rest: list[str], rules: ParseRules) -> tuple[str | None, str]:
    """(normalized name, "") or (None, why it cannot be a name)."""
    words = _strip_leading(rest, ARTICLES)
    name = normalize_name(" ".join(words))
    if words and words[0] in POINTER_WORDS:
        return None, f"'{name}' points at something instead of naming it; say e.g. 'this is my flask'"
    if name in NOT_NAMES:
        return None, f"'{name}' is not a name"
    problem = name_problem(name, max_chars=rules.max_name_chars, reserved=STATION_NAMES)
    if problem:
        if len(name) > rules.max_name_chars:
            problem += f" (labels.max_name_chars is {rules.max_name_chars})"
        return None, problem
    return name, ""


def _command_in_name(name: str, classes: Sequence[str], tags: Sequence[str], rules: ParseRules,
                     t: float) -> str | None:
    """Why `name` must not be taught although it is a valid name: it holds a wake word, or its words are a command
    themselves ("this is find the cup", "this is cancel"). None if neither."""
    words = name.split()
    for wake in sorted((words_of(w) for w in rules.wake_words), key=len, reverse=True):
        if wake and any(words[i:i + len(wake)] == wake for i in range(len(words) - len(wake) + 1)):
            return f"'{name}' holds the wake word '{' '.join(wake)}'; say one command at a time"
    inner = parse_utterance(name, classes, tags, dataclasses.replace(rules, wake_words=()), t,
                            wake_word_required=False)
    if inner.payload is not None:
        return f"'{name}' is a command itself ({inner.message}); say the name alone"
    return None


def _tag(kind: str, rest: list[str], classes: Sequence[str], tags: Sequence[str], rules: ParseRules,
         t: float) -> Parse:
    name, problem = _tag_name(rest, rules)
    if name is None:
        return _refuse(f"no name taken: {problem}")
    problem = _command_in_name(name, classes, tags, rules, t)
    if problem is not None:
        return _refuse(f"no name taken: {problem}")
    if name in tags:
        note = f"'{name}' is already a tag: this adds another view to it"
    elif name in classes:
        note = f"'{name}' is one of detector.classes: this adds a view to it"
    else:
        note = "a new name"
    if kind == TAG_VISUAL:
        return Parse(True, {"type": TAG_VISUAL, "name": name, "bbox": None, "t": t},
                     f"teach '{name}' from what vision is tracking now ({note})")
    return Parse(True, {"type": TAG_TEXT, "name": name, "prompt": name, "t": t},
                 f"learn '{name}' from the words '{name}' ({note})")


def _untag(rest: list[str], classes: Sequence[str], tags: Sequence[str], rules: ParseRules, t: float) -> Parse:
    name = normalize_name(" ".join(_strip_leading(rest, ARTICLES)))
    if not name:
        return _refuse("forget what? Say the name exactly")
    said = list(dict.fromkeys(tag for tag in tags if spoken_form(tag) == name))  # "mug (red)" is said "mug red"
    if len(said) > 1:
        return _refuse(f"'{name}' could be any of {', '.join(said)}, which are said the same way; not guessing "
                       "(send_command untag takes the exact name)")
    if said:
        tag = said[0]
        return Parse(True, {"type": UNTAG, "name": tag, "t": t},
                     f"forget the tag '{tag}'" + (" (its taught views only; the class stays)" if tag in classes else ""))
    if name in classes:
        return _refuse(f"'{name}' is one of detector.classes and has no taught views; nothing to forget")
    if not tags:
        return _refuse(f"nothing has been learned, so there is no '{name}' to forget")
    m = match_label(name.split(), tags, rules.label_cutoff, 0.0, what="tag")
    hint = f"; did you mean '{m.label}'? " if m.label else "; "
    return _refuse(f"nothing was learned as '{name}'{hint}forgetting needs the exact name (tags: {', '.join(tags)})")


_PHASE_VERBS = ("set", "change", "switch", "go", "to", "into")


def _phase_words(phrase: str) -> list[str] | None:
    """The words naming the phase in a phase command; [] when it names none ("set phase"); None if it is not one."""
    for pattern in _PHASE_PATTERNS:
        found = pattern.match(phrase)
        if found:
            words = _strip_leading(found.group("p").split(), ARTICLES)
            return [] if all(w in _PHASE_VERBS for w in words) else words
    return None


def _set_phase(words: list[str], rules: ParseRules, t: float) -> Parse:
    if not words:
        return _refuse(f"which phase? Say e.g. 'phase carry'; phases: {', '.join(PHASES)}")
    m = match_label(words, PHASES, rules.label_cutoff, rules.label_margin, what="phase")
    if m.label is None:
        return _refuse(m.note)
    return Parse(True, {"type": SET_PHASE, "phase": m.label, "t": t}, _with_note(f"set phase '{m.label}'", m.note))


def parse_utterance(text: str, classes: Sequence[str], tags: Sequence[str], rules: ParseRules, t: float,
                    wake_word_required: bool = True) -> Parse:
    """One utterance to one command payload, or the reason there is none. Pure: no I/O; `t` goes into the payload.

    `classes` are detector.classes, `tags` the operator's tag names. With rules.wake_words set and
    wake_word_required, an utterance that does not start with a wake word is not addressed and nothing else is
    looked at; with wake_word_required False a leading wake word is still dropped.
    """
    words = words_of(text)
    if rules.wake_words:
        rest = strip_wake_word(words, rules.wake_words)
        if rest is None and wake_word_required:
            return Parse(False, None, f"ignored: no wake word ({', '.join(rules.wake_words)})")
        words = words if rest is None else rest
    if len(str(text).strip()) > rules.max_utterance_chars:
        return _refuse(f"longer than voice.max_utterance_chars ({rules.max_utterance_chars}), so not a command")
    words = _strip_fillers(words)
    if not words:
        return _refuse("no command heard")
    phrase = " ".join(words)
    if phrase in CLEAR_PHRASES:
        return _clear(t)
    verb = _verb(words)
    if verb is not None:
        kind, rest = verb
        if kind == SELECT_TARGET:
            return _select(rest, classes, tags, rules, t)
        if kind in (TAG_VISUAL, TAG_TEXT):
            return _tag(kind, rest, classes, tags, rules, t)
        return _untag(rest, classes, tags, rules, t)
    phase = _phase_words(phrase)
    if phase is not None:
        return _set_phase(phase, rules, t)
    if "phase" in words:
        return _refuse(f"which phase? Say e.g. 'phase carry'; phases: {', '.join(PHASES)}")
    ways = ([f"to select it say 'find {phrase}'"] if phrase in {normalize_name(k) for k in known_labels(classes, tags)}
            else []) + ([f"to set the phase say 'phase {phrase}'"] if phrase in PHASES else [])
    if ways:
        return _refuse(f"'{phrase}' alone is not a command; {'; '.join(ways)}")
    return _refuse(f"not a command: '{phrase}'. Commands: {EXAMPLES}")


def grammar_text(rules: ParseRules) -> str:
    """Every phrase the parser understands, for --grammar and the README."""
    lines = [f"wake words (required for typed and piped lines unless --no-wake-word, always for speech heard by "
             f"--listen): {', '.join(rules.wake_words) or 'none'}",
             f"select_target <label>: {', '.join(VERBS[SELECT_TARGET])}",
             f"clear_target: {', '.join(CLEAR_PHRASES)}; also <select verb> {' / '.join(ANY_OBJECT)}",
             f"set_phase: {', '.join(PHASE_EXAMPLES)}; phases: {', '.join(PHASES)}",
             f"tag_visual <name>: {', '.join(VERBS[TAG_VISUAL])}",
             f"tag_text <name>: {', '.join(VERBS[TAG_TEXT])}",
             f"untag <name>: {', '.join(VERBS[UNTAG])}",
             f"dropped: leading {', '.join(_LEADING_FILLERS)}; trailing {', '.join(_TRAILING_FILLERS)}; "
             f"before a label: {', '.join(DETERMINERS)}; before a name: {', '.join(ARTICLES)}"]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------- the publisher

class EndpointBusy(Exception):
    """Another process holds the port."""


_LINGER_MS = 1000  # close() waits at most this long to hand a sent command over


class CommandPublisher:
    """Binds the voice endpoint as an XPUB, so it knows how many subscribers to `command` it has.

    With XPUB_VERBOSER every subscribe and unsubscribe of every subscriber is reported, including the unsubscribe
    when a subscriber goes away, so the count follows vision coming and going.
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._wanted = f"{TOPIC_COMMAND} ".encode("utf-8")
        self._subscribers = 0
        self._ctx = zmq.Context()
        self._sock = self._ctx.socket(zmq.XPUB)
        try:
            self._sock.set(zmq.XPUB_VERBOSER, 1)
            self._sock.set(zmq.LINGER, _LINGER_MS)
            self._sock.bind(endpoint)
        except zmq.ZMQError as exc:
            self.close()
            if exc.errno == zmq.EADDRINUSE:
                raise EndpointBusy(endpoint) from None
            raise

    def _drain(self, timeout_s: float) -> None:
        """Apply the subscription changes that arrived; wait up to timeout_s for the first one."""
        timeout_ms = max(0, int(timeout_s * 1000))
        while self._sock.poll(timeout_ms):
            frame = self._sock.recv()
            timeout_ms = 0
            if frame[:1] in (b"\x00", b"\x01") and self._wanted.startswith(frame[1:]):
                self._subscribers = max(0, self._subscribers + (1 if frame[:1] == b"\x01" else -1))

    @property
    def subscribers(self) -> int:
        """Subscribers to `command` right now."""
        self._drain(0)
        return self._subscribers

    def wait_for_subscriber(self, wait_s: float) -> int:
        """Subscribers, after waiting up to wait_s for the first one if there is none yet."""
        deadline = time.monotonic() + wait_s
        self._drain(0)
        while self._subscribers == 0:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            self._drain(left)
        return self._subscribers

    def publish(self, payload: dict, wait_s: float) -> int:
        """Send `payload` once to every subscriber; returns how many (0 = nobody subscribed within wait_s, and
        nothing was sent: commands are not queued)."""
        subscribers = self.wait_for_subscriber(wait_s)
        if subscribers:
            self._sock.send(encode(TOPIC_COMMAND, payload))
        return subscribers

    def close(self) -> None:
        self._sock.close()  # waits up to LINGER for a sent command to be handed over
        self._ctx.term()

    def __enter__(self) -> CommandPublisher:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# ------------------------------------------------------------------------------------------------- one utterance

SENT, NOT_DELIVERED, REFUSED, IGNORED = "sent", "not delivered", "refused", "ignored"


def _compact(payload: dict) -> str:
    return json.dumps(payload, separators=(",", ":"))


class VoiceSession:
    """Turns utterances into commands and prints, for each, what was understood and what was sent."""

    def __init__(self, classes: Sequence[str], rules: ParseRules, publisher: CommandPublisher | None,
                 read_tags: Callable[[], list[str]], out: TextIO, deliver_wait_s: float,
                 library_file: Path | str | None = None):
        self.classes = list(classes)
        self.rules = rules
        self.publisher = publisher            # None: understand and print only (--grammar checks, tests)
        self.read_tags = read_tags
        self.out = out
        self.deliver_wait_s = deliver_wait_s
        self.library_file = library_file      # named in warnings; None = "the label library"
        self._last_tag_error: str | None = None
        self._last_skipped: list[str] = []

    def note(self, text: str) -> None:
        print(text, file=self.out, flush=True)

    def _usable(self, name: object) -> bool:
        """A tag name voice can say and the label library would accept (an odd library.json can hold others)."""
        if not isinstance(name, str) or not spoken_form(name):
            return False
        return name_problem(normalize_name(name), max_chars=self.rules.max_name_chars, reserved=STATION_NAMES) is None

    def tags(self) -> list[str]:
        """The tag names now; [] with a warning (once per distinct problem) when the library cannot be read. Names
        voice cannot use are left out, with one warning line naming the file (again only when they change)."""
        try:
            tags = list(self.read_tags())
        except ValueError as exc:
            if str(exc) != self._last_tag_error:
                self.note(f"  warning: the label library cannot be read ({exc}); matching detector.classes only")
            self._last_tag_error = str(exc)
            return []
        self._last_tag_error = None
        skipped = [name for name in tags if not self._usable(name)]
        if skipped and skipped != self._last_skipped:
            where = self.library_file if self.library_file is not None else "the label library"
            self.note(f"  warning: {where} has tag names voice cannot use, left out: "
                      f"{', '.join(repr(name) for name in skipped)}")
        self._last_skipped = skipped
        return [name for name in tags if self._usable(name)]

    def handle(self, text: str, wake_word_required: bool = True, heard: str = "") -> str:
        """One utterance: SENT, NOT_DELIVERED, REFUSED or IGNORED."""
        self.note(f"> {text}{heard}")
        if not is_addressed(text, self.rules, wake_word_required):
            self.note(f"  ignored: no wake word ({', '.join(self.rules.wake_words)})")
            return IGNORED
        result = parse_utterance(text, self.classes, self.tags(), self.rules, clock.now(), wake_word_required)
        if result.payload is None:
            self.note(f"  nothing sent: {result.message}")
            return REFUSED
        self.note(f"  understood: {result.message}")
        if self.publisher is None:
            self.note(f"  not sent (no publisher): {_compact(result.payload)}")
            return NOT_DELIVERED
        subscribers = self.publisher.publish(result.payload, self.deliver_wait_s)
        if not subscribers:
            self.note(f"  NOT DELIVERED: nobody subscribed on {self.publisher.endpoint} within "
                        f"{self.deliver_wait_s:g} s, so it was dropped (commands are never queued). Is vision "
                        "running? Say it again once it is.")
            return NOT_DELIVERED
        self.note(f"  sent to {subscribers} subscriber{'' if subscribers == 1 else 's'}: {_compact(result.payload)}")
        return SENT


def run_lines(session: VoiceSession, lines: Iterable[str], wake_word_required: bool = True) -> int:
    """The stdin front end: one utterance per line until the end of input. Blank lines are skipped."""
    for line in lines:
        text = line.strip()
        if text:
            session.handle(text, wake_word_required)
    return 0


# ------------------------------------------------------------------------------------------------- --listen

LISTEN_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "voice_listen.ps1"
POWERSHELL = "powershell.exe"   # Windows PowerShell 5.1: System.Speech is part of .NET Framework, not PowerShell 7


# The select verbs the recognizer listens for: VERBS minus "target", which it heard for "forget" in the loopback
# test (so "forget my flask" became "target my flask"), and "get", which is short enough to be heard in anything.
SPOKEN_SELECT_VERBS = tuple(v for v in VERBS[SELECT_TARGET] if v not in ("target", "get"))


def listen_grammar(classes: Sequence[str], tags: Sequence[str], wake_words: Sequence[str], culture: str, *,
                   decoy: bool = False, adaptation: bool = True, dictated_names: bool = False,
                   decoy_words: Sequence[str] = ()) -> dict:
    """The stand-in recognizer's grammar: each rule a list of slots, each slot a list of alternatives ("" makes
    the slot optional). Only phrases the parser understands are in it, so the recognizer can only hear those.
    decoy: also load free dictation beside it, so speech outside the grammar lands there (and is never sent)
    instead of on the nearest command. adaptation: let the recognizer adapt to the speaker (its default).
    decoy_words: the exception to "only commands". Each is offered beside the wake words (when there are any) and
    beside the labels after a select verb, so that a word that sounds like one of them ("paula", "cap") can be
    heard as itself instead of as the wake word or the label; the parser sends nothing for it. A decoy word that
    is said like a wake word or a known label is left out.

    Tag names are free text. Without dictated_names the grammar covers select_target, clear_target, set_phase and
    untag of an existing tag, and a new tag is named by typing (stdin keeps working under --listen). With it,
    "this is <name>" and "learn <name>" take the name from dictation, which the loopback test caught misspelling
    names at high confidence ("red mug" as "red dog", 0.95). The slot "<dictation>" stands for free speech.
    """
    labels = known_labels(classes, tags)
    taken = {spoken_form(x) for x in (*labels, *wake_words)}
    decoys = [w for w in dict.fromkeys(decoy_words) if spoken_form(w) and spoken_form(w) not in taken]
    rules = [
        [list(SPOKEN_SELECT_VERBS), ["", "the", "my"], labels + decoys],
        [list(SPOKEN_SELECT_VERBS), ["anything", "any object"]],
        [list(CLEAR_PHRASES)],
        [["phase", "set phase to", "set the phase to"], list(PHASES)],
        [list(PHASES), ["phase"]],
    ]
    if tags:
        rules.append([["forget"], ["", "the"], list(tags)])
    if dictated_names:
        rules.append([["this is", "call this", "remember this as"], "<dictation>"])
        rules.append([["learn", "also look for"], "<dictation>"])
    wake = list(wake_words) + (decoys if wake_words else [])  # without wake words there is no slot for them
    return {"culture": culture, "wake": wake, "rules": rules, "decoy": decoy, "adaptation": adaptation}


class StandInRecognizer:
    """scripts/voice_listen.ps1 as a child process: grammar in on its stdin, one JSON line per recognition out."""

    def __init__(self, spec: dict, events: queue.Queue, wave_list: Path | None = None,
                 script: Path = LISTEN_SCRIPT):
        self.spec, self.events, self.wave_list, self.script = spec, events, wave_list, script
        self.proc: subprocess.Popen | None = None
        self.started = 0.0

    def start(self) -> None:
        if not self.script.is_file():
            raise FileNotFoundError(f"{self.script} is missing")
        args = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(self.script)]
        if self.wave_list is not None:
            args += ["-WaveList", str(self.wave_list)]
        self.started = time.monotonic()
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.proc.stdin.write(json.dumps(self.spec) + "\n")
        self.proc.stdin.close()
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                item = {"error": line.strip()}
            self.events.put(("recognizer", proc, item))
        proc.wait()
        self.events.put(("recognizer_ended", proc, {"code": proc.returncode, "stderr": proc.stderr.read().strip()}))

    def stop(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(5)


def _read_stdin(events: queue.Queue, stream: TextIO) -> None:
    for line in stream:
        events.put(("typed", None, line))
    events.put(("typed_end", None, None))


def run_listen(session: VoiceSession, cfg: Config, stdin: TextIO, wake_word_required: bool = True,
               recognizer: Callable[[dict, queue.Queue], StandInRecognizer] = StandInRecognizer) -> int:
    """The stand-in recognizer, plus typed lines on stdin. Rebuilds the grammar when the tag names change.
    Returns 1 when the recognizer cannot start or stops by itself, 0 on Ctrl+C."""
    listen = cfg.voice.listen
    events: queue.Queue = queue.Queue()

    def grammar(tags: list[str]) -> dict:
        return listen_grammar(session.classes, tags, cfg.voice.wake_words, listen.culture, decoy=listen.decoy,
                              adaptation=listen.adaptation, dictated_names=listen.dictated_names,
                              decoy_words=listen.decoy_words)

    def started(tags: list[str]) -> StandInRecognizer | None:
        rec = recognizer(grammar(tags), events)
        try:
            rec.start()
        except OSError as exc:  # no powershell.exe, or the script is missing
            session.note(f"voice: cannot start the stand-in recognizer ({exc}). It needs Windows PowerShell 5.1 "
                           f"(powershell.exe) and scripts/{LISTEN_SCRIPT.name}. Without --listen, typed or piped "
                           "lines work.")
            return None
        return rec

    tags = session.tags()
    rec = started(tags)
    if rec is None:
        return 1
    threading.Thread(target=_read_stdin, args=(events, stdin), daemon=True).start()
    session.note(f"voice: starting the stand-in recognizer (Windows System.Speech, {listen.culture}); it hears only "
                   f"the command grammar{' after a wake word' if cfg.voice.wake_words else ''}. Typed lines work too.")
    next_check = time.monotonic() + listen.labels_refresh_s
    try:
        while True:
            try:
                kind, proc, item = events.get(timeout=0.25)  # short, so Ctrl+C is noticed promptly on Windows
            except queue.Empty:
                kind = None
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + listen.labels_refresh_s
                now_tags = session.tags()
                if now_tags != tags:
                    tags = now_tags
                    rec.stop()
                    new = started(tags)
                    if new is None:
                        return 1
                    rec = new
                    session.note(f"voice: the tags changed ({', '.join(tags) or 'none'}); grammar rebuilt")
            if kind is None or (proc is not None and proc is not rec.proc):
                continue  # a recognizer that was replaced
            if kind == "typed":
                if item.strip():
                    session.handle(item.strip(), wake_word_required)
            elif kind == "recognizer_ended":
                session.note(f"voice: the stand-in recognizer stopped (exit {item['code']}): "
                               f"{item['stderr'] or 'no message'}")
                return 1
            elif kind == "recognizer":
                if "ready_ms" in item:
                    session.note(f"voice: listening (recognizer ready {1000 * (time.monotonic() - rec.started):.0f} "
                                   f"ms after start, {item['ready_ms']} ms of it loading the grammar)")
                elif "error" in item:
                    session.note(f"voice: recognizer: {item['error']}")
                elif item.get("text"):
                    confidence = float(item.get("confidence", 0.0))
                    heard = f"   [heard, confidence {confidence:.2f}]"
                    if item.get("grammar") == "decoy":
                        session.note(f"> {item['text']}   [heard outside the commands, confidence {confidence:.2f}]")
                        session.note("  ignored: not one of the command phrases (voice.listen.decoy)")
                    elif confidence < listen.min_confidence:
                        session.note(f"> {item['text']}{heard}")
                        session.note(f"  ignored: confidence below voice.listen.min_confidence "
                                       f"({listen.min_confidence:.2f})")
                    else:
                        session.handle(item["text"], True, heard)
    except KeyboardInterrupt:
        return 0
    finally:
        rec.stop()


# ------------------------------------------------------------------------------------------------- command line

def _say(text: str) -> None:
    print(f"voice: {text}", file=sys.stderr, flush=True)


def main(argv: Sequence[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m paul_vision.voice", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="path to config.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--say", metavar="TEXT", help="one utterance, then exit: 0 sent, 1 not sent")
    mode.add_argument("--listen", action="store_true",
                      help="the local stand-in recognizer (Windows System.Speech); typed lines still work")
    mode.add_argument("--grammar", action="store_true", help="print the phrases understood, and exit")
    parser.add_argument("--no-wake-word", action="store_true",
                        help="every typed or piped line counts without a wake word, also under --listen; speech "
                             "heard by --listen still needs one (--say never needs one)")
    args = parser.parse_args(argv)
    stdin = stdin if stdin is not None else sys.stdin
    out = stdout if stdout is not None else sys.stdout
    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError, ValueError) as exc:
        _say(f"configuration: {exc}")
        return 2
    rules = ParseRules.from_config(cfg)
    if args.grammar:
        print(grammar_text(rules), file=out, flush=True)
        return 0
    try:
        publisher = CommandPublisher(cfg.voice.endpoint)
    except EndpointBusy:
        _say(f"{cfg.voice.endpoint} (voice.endpoint) is in use by another process; is another voice process "
             "running?")
        return 1
    except zmq.ZMQError as exc:
        _say(f"cannot bind {cfg.voice.endpoint} (voice.endpoint): {exc}")
        return 1
    directory = library_dir(cfg)
    session = VoiceSession(cfg.detector.classes, rules, publisher, lambda: read_tag_names(directory), out,
                           cfg.voice.deliver_wait_s, library_file=directory / LIBRARY_FILE)
    try:
        if args.say is not None:
            return 0 if session.handle(args.say, wake_word_required=False) == SENT else 1
        wake = ", ".join(cfg.voice.wake_words)
        need_wake = bool(cfg.voice.wake_words) and not args.no_wake_word
        session.note(f"voice: bound {cfg.voice.endpoint}; vision connects here. Commands said before vision has "
                       "subscribed are dropped, not queued. "
                       + (f"Start each utterance with a wake word ({wake}). " if need_wake else "")
                       + "Known labels: " + ", ".join(known_labels(session.classes, session.tags())) + ".")
        if args.listen:
            return run_listen(session, cfg, stdin, not args.no_wake_word)
        session.note("voice: one utterance per line; Ctrl+Z then Enter (or Ctrl+C) ends.")
        return run_lines(session, stdin, not args.no_wake_word)
    except KeyboardInterrupt:
        return 0
    finally:
        publisher.close()


if __name__ == "__main__":
    if hasattr(sys.stdin, "reconfigure") and not sys.stdin.isatty():
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # a piped recognizer writes UTF-8
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")                    # never die printing an odd character
    sys.exit(main())
