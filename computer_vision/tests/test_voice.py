import dataclasses
import io
import itertools
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import yaml
import zmq

from paul_vision import commands, voice
from paul_vision.commands import CommandReceiver
from paul_vision.config import (DEFAULT_CONFIG_PATH, ConfigError, VoiceConfig, VoiceListenConfig, from_dict,
                                load_config, validate_voice_config)
from paul_vision.labels import Library, Tag, View, write_library
from paul_vision.phase import PHASES
from paul_vision.voice import (IGNORED, NOT_DELIVERED, REFUSED, SENT, CommandPublisher, EndpointBusy, ParseRules,
                               VoiceSession, listen_grammar, match_label, parse_utterance, words_of)

CLASSES = ["cup", "bottle", "pill bottle", "box"]
RULES = ParseRules(wake_words=("paul", "hey paul", "ok paul"), label_cutoff=0.8, label_margin=0.1,
                   max_utterance_chars=200, max_name_chars=40)
NO_WAKE = ParseRules(wake_words=(), label_cutoff=0.8, label_margin=0.1, max_utterance_chars=200, max_name_chars=40)
T = 1731542400.25


def parse(text, tags=(), rules=RULES, required=True):
    return parse_utterance(text, CLASSES, list(tags), rules, T, required)


def command(text, tags=(), rules=RULES, required=True):
    """The payload without its `t`, or None; every payload carries the `t` it was given."""
    result = parse(text, tags, rules, required)
    if result.payload is None:
        return None
    assert result.payload["t"] == T
    return {k: v for k, v in result.payload.items() if k != "t"}


def free_endpoint() -> str:
    """A port nothing listens on, found by the OS; never one of the shipped 5555 to 5560."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def poll_for(receiver: CommandReceiver, seconds: float, count: int = 1) -> list:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline and len(out) < count:
        out.extend(receiver.poll())
        time.sleep(0.01)
    return out


def select(label):
    return {"type": "select_target", "label": label}


CLEAR = {"type": "clear_target"}


def phase(p):
    return {"type": "set_phase", "phase": p}


def tag_visual(name):
    return {"type": "tag_visual", "name": name, "bbox": None}


def tag_text(name):
    return {"type": "tag_text", "name": name, "prompt": name}


def untag(name):
    return {"type": "untag", "name": name}


# --- the parser: each command ---

@pytest.mark.parametrize("text, tags, expected", [
    # select_target
    ("paul find the cup", (), select("cup")),
    ("paul select bottle", (), select("bottle")),
    ("paul look for the pill bottle", (), select("pill bottle")),
    ("paul target my flask", ("my flask",), select("my flask")),
    ("paul search for a box", (), select("box")),
    ("paul pick up the cup", (), select("cup")),
    ("Paul, FIND the Cup!", (), select("cup")),
    ("hey paul, find the cup.", (), select("cup")),
    ("ok paul track the bottle", (), select("bottle")),
    ("paul please find the cup thank you", (), select("cup")),
    ("paul find my cup", (), select("cup")),                 # a determiner before a class
    ("paul find the cups", (), select("cup")),               # plain plurals
    ("paul find boxes", (), select("box")),
    ("paul find the bottles", (), select("bottle")),
    ("paul find the red mugs", ("red mug",), select("red mug")),
    ("paul find the bottel", (), select("bottle")),          # near misses above the cutoff (0.83)
    ("paul look for the pillbottle", (), select("pill bottle")),  # 0.95
    ("paul find the pil bottle", (), select("pill bottle")),      # 0.95 against 0.75 for "bottle"
    # clear_target
    ("paul any object", (), CLEAR),
    ("paul clear target", (), CLEAR),
    ("paul clear the target", (), CLEAR),
    ("paul never mind", (), CLEAR),
    ("paul nevermind", (), CLEAR),
    ("paul anything", (), CLEAR),
    ("paul cancel", (), CLEAR),
    ("paul find anything", (), CLEAR),
    ("paul look for any object", (), CLEAR),
    ("paul find whatever", (), CLEAR),
    # set_phase
    ("paul phase carry", (), phase("carry")),
    ("paul set phase to place", (), phase("place")),
    ("paul set the phase to grasp", (), phase("grasp")),
    ("paul change phase to release", (), phase("release")),
    ("paul phase is approach", (), phase("approach")),
    ("paul carry phase", (), phase("carry")),
    ("paul switch to release phase", (), phase("release")),
    ("paul go to the place phase", (), phase("place")),
    ("paul phase search", (), phase("search")),
    ("paul phase graps", (), phase("grasp")),                # near miss above the cutoff
    # tag_visual: the name as said, lower case, single spaces; "my" stays, a / an / the go
    ("paul this is my flask", (), tag_visual("my flask")),
    ("paul call this my flask", (), tag_visual("my flask")),
    ("paul remember this as my flask", (), tag_visual("my flask")),
    ("paul this is called  My   Flask", (), tag_visual("my flask")),
    ("paul this is a red mug", (), tag_visual("red mug")),
    ("paul call it the keys", (), tag_visual("keys")),
    ("paul label this as david's mug", (), tag_visual("david's mug")),
    ("paul learn this as my flask", (), tag_visual("my flask")),  # not tag_text "this as my flask"
    # tag_text: the name is the prompt
    ("paul learn red mug", (), tag_text("red mug")),
    ("paul also look for a red mug", (), tag_text("red mug")),
    ("paul learn to find the blue box", (), tag_text("blue box")),
    ("paul also find a green cup", (), tag_text("green cup")),
    # untag: exact names only
    ("paul forget my flask", ("my flask",), untag("my flask")),
    ("paul unlearn red mug", ("red mug", "my flask"), untag("red mug")),
    ("paul forget about the red mug", ("red mug",), untag("red mug")),
    ("paul forget cup", ("cup",), untag("cup")),             # a tag that adds views to a class
])
def test_each_command(text, tags, expected):
    result = parse(text, tags)
    assert result.addressed
    assert command(text, tags) == expected, result.message


# --- the parser: refusals, which send nothing and say why ---

@pytest.mark.parametrize("text, tags, why", [
    ("paul find the cop", (), "no known label like"),          # 0.67: "cap" would score the same
    ("paul find the mug", (), "no known label like"),
    ("paul find the elephant", (), "no known label like"),
    ("paul find the plays", (), "no known label like"),
    ("paul find red tug", ("red mug", "red jug"), "'tug' is not"),  # 0.86 to both, but no word is 'tug'
    ("paul find", (), "select what"),
    ("paul find the", (), "select what"),
    ("paul", (), "no command"),
    ("paul please", (), "no command"),
    ("paul cup", (), "alone is not a command"),
    ("paul carry", (), "say 'phase carry'"),
    ("paul phase", (), "which phase"),
    ("paul set phase", (), "which phase"),
    ("paul phase carrie", (), "no known phase like"),          # 0.73
    ("paul phase plays", (), "no known phase like"),           # 0.60
    ("paul phase lunch", (), "no known phase like"),
    ("paul what time is it", (), "not a command"),
    ("paul thank you", (), "not a command"),
    ("paul ###", (), "not a command"),
    ("paul \x00\x01", (), "not a command"),
    ("paul this is", (), "empty"),
    ("paul this is it", (), "points at"),
    ("paul this is that cup", (), "points at"),
    ("paul call this this", (), "points at"),
    ("paul learn this", (), "points at"),
    ("paul this is anything", (), "not a name"),
    ("paul this is a/b", (), "slash"),
    ("paul learn mug #3", (), "hash"),
    ("paul this is drop station", (), "station"),
    ("paul this is the pick station", (), "station"),
    ("paul this is " + "x" * 41, (), "too long"),
    ("paul this is my\x07flask", (), "cannot be used"),
    ("paul forget", (), "forget what"),
    ("paul forget cup", (), "detector.classes"),
    ("paul forget mug", (), "nothing has been learned"),
    ("paul forget my flsk", ("my flask",), "did you mean 'my flask'"),
    ("paul forget flask", ("my flask",), "exact name"),       # destructive: no fuzzy, no dropped "my"
    ("paul " + "find the cup " * 20, (), "max_utterance_chars"),
])
def test_refusals_send_nothing_and_say_why(text, tags, why):
    result = parse(text, tags)
    assert result.addressed and result.payload is None
    assert why in result.message


def test_a_name_at_the_limit_is_taken():
    assert command("paul this is " + "x" * 40) == tag_visual("x" * 40)


# --- the wake word ---

@pytest.mark.parametrize("text", ["find the cup", "pauline find the cup", "hey find the cup", "", "   ",
                                  "the cup paul", "we should find paul a cup"])
def test_without_a_wake_word_nothing_is_addressed(text):
    result = parse(text)
    assert not result.addressed and result.payload is None and "wake word" in result.message


@pytest.mark.parametrize("text", ["paul find the cup", "hey paul find the cup", "ok paul, find the cup",
                                  "PAUL: find the cup", "Hey, Paul! Find the cup."])
def test_each_wake_word_is_dropped(text):
    assert command(text) == select("cup")


def test_wake_word_not_required_still_drops_one():
    assert command("find the cup", required=False) == select("cup")
    assert command("paul find the cup", required=False) == select("cup")


def test_no_wake_words_configured_means_every_utterance_counts():
    assert command("find the cup", rules=NO_WAKE) == select("cup")
    assert command("paul find the cup", rules=NO_WAKE) is None  # "paul" is then just a word
    assert parse("", rules=NO_WAKE).message == "no command heard"


def test_the_longest_wake_word_wins():
    # "hey paul" must not leave "hey" or "paul" behind as part of the command
    rules = ParseRules(("paul", "hey paul"), 0.8, 0.1, 200, 40)
    assert parse_utterance("hey paul find the cup", CLASSES, [], rules, T).payload["label"] == "cup"


# --- collisions ---

def test_an_exact_name_wins_over_the_same_name_without_my():
    tags = ("flask", "my flask")
    assert command("paul find my flask", tags) == select("my flask")
    assert command("paul find the flask", tags) == select("flask")
    assert command("paul find flask", tags) == select("flask")


def test_a_near_miss_between_flask_and_my_flask_is_refused():
    result = parse("paul find my flsk", ("flask", "my flask"))  # 0.93 against 0.89
    assert result.payload is None and "could be 'my flask'" in result.message and "'flask'" in result.message


def test_a_near_miss_clearly_closer_to_one_is_taken():
    assert command("paul find the flsk", ("flask", "my flask")) == select("flask")  # 0.89 against 0.67


def test_a_tag_named_like_a_phase():
    tags = ("carry",)
    assert command("paul find carry", tags) == select("carry")
    assert command("paul phase carry", tags) == phase("carry")
    assert command("paul carry phase", tags) == phase("carry")
    assert command("paul this is carry") == tag_visual("carry")  # a phase name may be a tag name
    bare = parse("paul carry", tags)
    assert bare.payload is None and "find carry" in bare.message and "phase carry" in bare.message


def test_a_tag_named_like_a_class_is_one_label():
    assert command("paul find the cup", ("cup",)) == select("cup")
    result = parse("paul this is a cup")
    assert result.payload["name"] == "cup" and "detector.classes" in result.message


def test_bottle_and_pill_bottle_do_not_collide():
    assert command("paul find bottle") == select("bottle")
    assert command("paul find pill bottle") == select("pill bottle")


def test_the_longer_verb_phrase_wins():
    assert command("paul also look for the cup") == tag_text("cup")    # not select_target
    assert command("paul look for the cup") == select("cup")
    assert command("paul this is called max") == tag_visual("max")      # not "called max"
    assert command("paul forget about it", ("it",)) == untag("it")


def test_an_existing_tag_name_is_sent_and_vision_decides():
    result = parse("paul this is my flask", ("my flask",))
    assert result.payload is not None and "already a tag" in result.message


def test_a_tag_is_named_as_a_tag_in_the_message():
    assert "a tag" in parse("paul find my flask", ("my flask",)).message
    assert "a tag" not in parse("paul find the cup").message


def test_a_fuzzy_match_says_how_it_was_heard():
    assert "similarity 0.83" in parse("paul find the bottel").message


def test_no_verb_phrase_belongs_to_two_commands():
    phrases = [p for ps in voice.VERBS.values() for p in ps]
    assert len(phrases) == len(set(phrases))
    assert all(p == " ".join(p.split()) and p == p.lower() for p in phrases)


# --- matching labels (pure) ---

@pytest.mark.parametrize("words, known, label", [
    ("cup", ["cup"], "cup"),
    ("the cup", ["cup"], "cup"),
    ("pillbottle", ["pill bottle", "bottle"], "pill bottle"),
    ("bottel", ["cup", "bottle"], "bottle"),
    ("cop", ["cup", "cap"], None),
    ("cop", ["cup"], None),                  # 0.67 is below the cutoff even with no rival
    ("red tug", ["red mug", "red jug"], None),
    ("", ["cup"], None),
    ("cup", [], None),
])
def test_match_label(words, known, label):
    assert match_label(words.split(), known, 0.8, 0.1).label == label


def test_match_label_sends_the_label_as_it_is_known():
    assert match_label(["pill", "bottle"], ["Pill Bottle"], 0.8, 0.1).label == "Pill Bottle"


def test_words_keep_slash_and_hash_and_drop_sentence_punctuation():
    assert words_of("Paul, find: “the” cup!?") == ["paul", "find", "the", "cup"]
    assert words_of("this is a/b #2") == ["this", "is", "a/b", "#2"]
    assert words_of("ＣＵＰ") == ["cup"]  # full-width letters, as some recognizers write them


# --- near matches that are another word: refused, naming the closest label ---

STRICT = dataclasses.replace(RULES, label_cutoff=0.9)  # the shipped voice.label_cutoff

# Each was sent as the other label before (reviewer's list); one changed letter makes another real word.
MISHEARD = [
    ("paul find bin 2", ("bin 1",), "bin 1"),
    ("paul find shelf b", ("shelf a",), "shelf a"),
    ("paul find the red jug", ("red mug",), "red mug"),
    ("paul find the mouse", ("house",), "house"),
    ("paul find the class", ("glass",), "glass"),
    ("paul find the clock", ("block",), "block"),
    ("paul find the lock", ("block",), "block"),
    ("paul find the black", ("block",), "block"),
    ("paul find the small box", ("tall box",), "tall box"),
    ("paul phase replace", (), "place"),
    ("paul set phase to research", (), "search"),
    ("paul find the cupp", (), "cup"),
    ("paul find the bottel", (), "bottle"),
]


@pytest.mark.parametrize("text, tags, closest", MISHEARD)
def test_a_near_match_that_may_be_another_word_is_refused(text, tags, closest, shipped_cfg):
    for rules in (STRICT, ParseRules.from_config(shipped_cfg)):
        result = parse(text, tags, rules)
        assert result.addressed and result.payload is None
        assert f"closest '{closest}' scores 0." in result.message and "\n" not in result.message


@pytest.mark.parametrize("words, known, why", [
    ("bin 2", ["bin 1"], "but the numbers differ"),
    ("the bottle 3", ["bottle 2"], "but the numbers differ"),
    ("shelf b", ["shelf a"], "but 'b' is not 'a'"),
    ("red jug", ["red mug"], "but 'jug' is not 'mug'"),
    ("big box", ["pig box"], "but 'big' is not 'pig'"),
    ("small box", ["tall box"], "but 'small' is not 'tall'"),
    ("bo x", ["box"], "but 'x' must be said exactly"),
])
def test_a_word_or_number_that_differs_is_refused_whatever_the_score(words, known, why):
    m = match_label(words.split(), known, 0.8, 0.1)  # each scores 0.8 or more as a whole
    assert m.label is None and why in m.note and f"closest '{known[0]}'" in m.note


@pytest.mark.parametrize("text, tags, label", [
    ("paul find pillbottle", (), "pill bottle"),       # 0.95
    ("paul find the cups", (), "cup"),
    ("paul find bottles", (), "bottle"),
    ("paul find the boxes", (), "box"),
    ("paul find a cup", (), "cup"),
    ("paul find the pill bottle", (), "pill bottle"),
    ("paul find my flasks", ("my flask",), "my flask"),
    ("paul find the red cup", ("red cups",), "red cups"),  # 0.93; 'cup' and 'cups' are one word
])
def test_exact_plural_and_article_forms_still_match(text, tags, label):
    assert command(text, tags, STRICT) == select(label)


def test_the_margin_still_refuses_two_near_matches():
    result = parse("paul find pillbottle", ("pill bottles",), STRICT)  # 0.95 against 0.91
    assert result.payload is None and "could be 'pill bottle' (0.95) or 'pill bottles' (0.91)" in result.message


def test_a_phase_said_exactly_still_matches():
    for p in PHASES:
        assert command(f"paul phase {p}", rules=STRICT) == phase(p)


# --- names that are commands ---

@pytest.mark.parametrize("text, tags, why", [
    ("paul this is my flask paul find the cup", (), "'my flask paul find the cup' holds the wake word 'paul'"),
    ("paul this is my paul", (), "holds the wake word 'paul'"),
    ("paul this is hey paul", (), "holds the wake word 'hey paul'"),
    ("paul learn ok paul", (), "holds the wake word 'ok paul'"),
    ("paul this is cancel", (), "'cancel' is a command itself (clear target"),
    ("paul this is never mind", (), "'never mind' is a command itself (clear target"),
    ("paul call it clear", (), "'clear' is a command itself"),
    ("paul this is find the cup", (), "'find the cup' is a command itself (select target 'cup')"),
    ("paul learn find the cup", (), "'find the cup' is a command itself (select target 'cup')"),
    ("paul learn select the cup", (), "is a command itself (select target 'cup')"),
    ("paul this is phase carry", (), "'phase carry' is a command itself (set phase 'carry')"),
    ("paul this is carry phase", (), "is a command itself (set phase 'carry')"),
    ("paul learn forget my flask", ("my flask",), "is a command itself (forget the tag 'my flask')"),
    ("paul this is track ball", ("ball",), "'track ball' is a command itself (select target 'ball'"),
])
def test_a_name_that_is_a_command_is_refused_in_one_line(text, tags, why):
    result = parse(text, tags)
    assert result.addressed and result.payload is None
    assert result.message.startswith("no name taken: ") and why in result.message and "\n" not in result.message
    s, out = session(tags=lambda: list(tags))
    assert s.handle(text) == REFUSED and s.publisher.sent == []


@pytest.mark.parametrize("text, payload", [
    ("paul this is track ball", tag_visual("track ball")),   # 'track' is a verb, but nothing is called ball
    ("paul this is wrong", tag_visual("wrong")),             # a known limit: taken as a name
    ("paul this is carry", tag_visual("carry")),
    ("paul this is the find", tag_visual("find")),
    ("paul this is pauline", tag_visual("pauline")),         # a wake word only as a whole word
    ("paul this is ok", tag_visual("ok")),
    ("paul learn to find the blue box", tag_text("blue box")),
    ("paul also look for the red mug", tag_text("red mug")),
])
def test_a_name_that_only_looks_like_a_command_is_taken(text, payload):
    assert command(text) == payload


def test_names_are_checked_against_the_wake_words_in_use():
    assert command("paul this is hey there") == tag_visual("hey there")
    assert command("this is my paul", rules=NO_WAKE) == tag_visual("my paul")  # no wake words: 'paul' is a word


# --- tag names with punctuation, compared as they are said ---

@pytest.mark.parametrize("tag, said", [("mug (red)", "mug red"), ("mug (red)", "mug (red)"), ("no. 5", "no 5"),
                                       ("no. 5", "no. 5"), ("v2.0", "v2.0"), ("v2.0", "v2 0")])
def test_a_tag_with_punctuation_is_found_and_forgotten_as_it_is_said(tag, said):
    assert command(f"paul find {said}", (tag,), STRICT) == select(tag)
    assert command(f"paul find the {said}", (tag,), STRICT) == select(tag)
    assert command(f"paul forget {said}", (tag,), STRICT) == untag(tag)
    assert command(f"paul forget the {said}", (tag,), STRICT) == untag(tag)


def test_two_tags_said_the_same_way_are_refused_naming_both():
    tags = ("mug (red)", "mug red")
    found = parse("paul find mug red", tags)
    assert found.payload is None
    assert "'mug red' could be any of mug (red), mug red, which are said the same way; not guessing" in found.message
    forget = parse("paul forget mug red", tags)
    assert forget.payload is None and "mug (red), mug red" in forget.message and "send_command untag" in forget.message


def test_forgetting_a_punctuated_tag_is_never_fuzzy():
    result = parse("paul forget mug rad", ("mug (red)",))
    assert result.payload is None and "forgetting needs the exact name" in result.message


# --- tag names in library.json that voice cannot use ---

def test_unusable_tag_names_are_left_out_with_one_warning_naming_the_file(tmp_path):
    names = ["my flask", "", "....", "a/b", "mug #3", "x" * 41, "drop station", 5, None, "bad\x07name"]
    out = io.StringIO()
    library_file = tmp_path / "labels" / "library.json"
    s = VoiceSession(CLASSES, RULES, FakePublisher(), lambda: list(names), out, deliver_wait_s=0.1,
                     library_file=library_file)
    assert s.tags() == ["my flask"]
    assert s.handle("paul find my flask") == SENT
    assert s.handle("paul forget my flask") == SENT
    text = out.getvalue()
    assert text.count("warning:") == 1
    line = next(line for line in text.splitlines() if "warning:" in line)
    assert str(library_file) in line and "tag names voice cannot use, left out:" in line
    assert all(repr(n) in line for n in names[1:])
    names.append("b/c")  # a new one: said again, once
    s.tags()
    s.tags()
    assert out.getvalue().count("warning:") == 2


def test_without_a_file_the_warning_names_the_library():
    s, out = session(tags=lambda: ["", "cup holder"])
    assert s.tags() == ["cup holder"]
    assert "warning: the label library has tag names voice cannot use, left out: ''" in out.getvalue()


def test_listen_starts_with_a_library_holding_unusable_names(tmp_path):
    FakeRecognizer.script = [{"text": "paul find my flask", "confidence": 0.95}]
    FakeRecognizer.specs = []
    cfg = listen_cfg(tmp_path, free_endpoint())
    s, out = session(tags=lambda: ["my flask", "", "....", " "])
    assert voice.run_listen(s, cfg, io.StringIO(""), recognizer=FakeRecognizer) == 1
    assert [p["label"] for p in s.publisher.sent] == ["my flask"]
    rules = FakeRecognizer.specs[0]["rules"]
    assert rules[0][2] == [*CLASSES, "my flask"]          # the labels slot: no empty choice
    forget = next(rule for rule in rules if rule[0] == ["forget"])
    assert forget[2] == ["my flask"]
    assert "left out" in out.getvalue()


# --- the type strings and names shared with other modules ---

def test_vision_accepts_every_payload_voice_makes():
    # Each kind of payload, through vision's own apply_command (commands.py), with hooks that only record.
    seen = []
    hooks = commands.CommandHooks(classes=lambda: CLASSES + ["my flask"],
                                  set_preferred_label=lambda label: seen.append(("target", label)),
                                  set_phase=lambda p: seen.append(("phase", p)),
                                  teach=lambda request: seen.append(request) or True)
    for text in ("paul find my flask", "paul find the cup", "paul any object", "paul phase grasp",
                 "paul this is red mug", "paul learn blue box", "paul forget my flask"):
        assert commands.apply_command(parse(text, ["my flask"]).payload, hooks) is True, text
    assert seen[:4] == [("target", "my flask"), ("target", "cup"), ("target", None), ("phase", "grasp")]
    assert [(r.kind, r.name, r.bbox, r.prompt, r.problem) for r in seen[4:]] == [
        ("tag_visual", "red mug", None, None, None),     # bbox null: the target vision is tracking
        ("tag_text", "blue box", None, "blue box", None),
        ("untag", "my flask", None, None, None)]


def test_station_names_match_aruco():
    from paul_vision.aruco import STATION_DROP, STATION_PICK  # imports OpenCV; voice itself must not
    assert set(voice.STATION_NAMES) == {STATION_PICK, STATION_DROP}


def test_voice_imports_no_torch_ultralytics_or_opencv():
    code = ("import sys, paul_vision.voice; "
            "print(sorted(m for m in ('torch', 'ultralytics', 'cv2') if m in sys.modules))")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          cwd=Path(__file__).parents[1])
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


# --- the stand-in recognizer's grammar ---

def _phrases(spec, dictated=("red mug", "the blue box")):
    """Every utterance the grammar can produce, with these standing in for free dictation."""
    for wake, rule in itertools.product(spec["wake"] or [""], spec["rules"]):
        slots = [list(dictated) if slot == "<dictation>" else slot for slot in rule]
        for parts in itertools.product(*slots):
            yield " ".join(p for p in (wake, *parts) if p)


def test_everything_the_recognizer_can_hear_is_a_command():
    tags = ["my flask", "carry"]
    spec = listen_grammar(CLASSES, tags, list(RULES.wake_words), "en-US")
    phrases = list(_phrases(spec))
    assert len(phrases) > 300
    for text in phrases:
        assert parse(text, tags).payload is not None, text


def test_dictated_names_become_tag_commands():
    spec = listen_grammar(CLASSES, [], ["paul"], "en-US", dictated_names=True)
    kinds = {}
    for text in _phrases(spec):
        payload = parse(text).payload
        assert payload is not None, text
        kinds.setdefault(payload["type"], set()).add(payload.get("name"))
    assert kinds["tag_visual"] == kinds["tag_text"] == {"red mug", "blue box"}


def test_the_recognizer_grammar_holds_the_labels_and_rebuilds_with_the_tags():
    without = listen_grammar(CLASSES, [], ["paul"], "en-US")
    with_tag = listen_grammar(CLASSES, ["my flask"], ["paul"], "en-US")
    assert any("paul find the my flask" == p for p in _phrases(with_tag))
    assert not any("my flask" in p for p in _phrases(without))
    assert any(p.startswith("paul forget") for p in _phrases(with_tag))
    assert not any(p.startswith("paul forget") for p in _phrases(without))
    assert all(not p.startswith(("paul this is", "paul learn")) for p in _phrases(with_tag))  # no free names
    assert all(slot != "<dictation>" for rule in with_tag["rules"] for slot in rule)
    assert with_tag["decoy"] is False and listen_grammar(CLASSES, [], [], "en-US", decoy=True)["decoy"] is True


def test_the_recognizer_never_listens_for_target_or_get():
    # The loopback test heard "forget my flask" as "target my flask"; typed lines keep both verbs.
    spec = listen_grammar(CLASSES, ["my flask"], ["paul"], "en-US")
    first_words = {p.split()[1] for p in _phrases(spec)}
    assert "target" not in first_words and "get" not in first_words
    assert set(voice.SPOKEN_SELECT_VERBS) < set(voice.VERBS["select_target"])
    assert command("paul target the cup") == select("cup") and command("paul get the cup") == select("cup")


def test_decoy_words_sit_beside_the_wake_words_and_the_select_labels():
    spec = listen_grammar(CLASSES, ["my flask"], ["paul", "hey paul"], "en-US", decoy_words=["bowl", "paula"])
    assert spec["wake"] == ["paul", "hey paul", "bowl", "paula"]
    assert spec["rules"][0][2] == [*CLASSES, "my flask", "bowl", "paula"]
    assert all("bowl" not in slot for rule in spec["rules"][1:] for slot in rule)  # phases, clear, forget: none
    assert listen_grammar(CLASSES, [], ["paul"], "en-US")["wake"] == ["paul"]  # none by default


def test_a_decoy_word_said_like_a_wake_word_or_a_label_is_left_out():
    spec = listen_grammar(CLASSES, ["my flask"], ["paul"], "en-US",
                          decoy_words=["cup", "paul", "my flask", "pill bottle", "bowl", "bowl"])
    assert spec["wake"] == ["paul", "bowl"]
    assert spec["rules"][0][2] == [*CLASSES, "my flask", "bowl"]


def test_without_wake_words_decoy_words_get_no_wake_slot():
    # The script makes the wake slot required whenever it is not empty; decoys alone would demand a decoy first.
    spec = listen_grammar(CLASSES, [], [], "en-US", decoy_words=["bowl", "paula"])
    assert spec["wake"] == []
    assert spec["rules"][0][2] == [*CLASSES, "bowl", "paula"]


def test_a_phrase_with_a_shipped_decoy_word_sends_nothing(shipped_cfg):
    rules = ParseRules.from_config(shipped_cfg)
    decoys = shipped_cfg.voice.listen.decoy_words
    tags = ["my flask"]
    spec = listen_grammar(shipped_cfg.detector.classes, tags, shipped_cfg.voice.wake_words, "en-US",
                          decoy_words=decoys)
    plain = listen_grammar(shipped_cfg.detector.classes, tags, shipped_cfg.voice.wake_words, "en-US")
    with_decoy = [p for p in _phrases(spec) if set(p.split()) & set(decoys)]
    for text in with_decoy:
        assert parse_utterance(text, shipped_cfg.detector.classes, tags, rules, T).payload is None, text
    assert len(with_decoy) > 100
    # The decoy words only add phrases: every other phrase is the one the grammar without them has.
    assert sorted(p for p in _phrases(spec) if p not in with_decoy) == sorted(_phrases(plain))


# --- the publisher, over a real socket on a free port ---

def test_a_command_reaches_vision_that_connected_first():
    endpoint = free_endpoint()
    with CommandReceiver([free_endpoint(), endpoint]) as receiver, CommandPublisher(endpoint) as pub:
        payload = {"type": "select_target", "label": "cup", "t": T}
        assert pub.publish(payload, wait_s=3.0) == 1
        assert poll_for(receiver, 2.0) == [payload]


def test_a_command_reaches_vision_that_connects_later():
    endpoint = free_endpoint()
    with CommandPublisher(endpoint) as pub, CommandReceiver(endpoint) as receiver:
        payload = {"type": "clear_target", "t": T}
        assert pub.publish(payload, wait_s=3.0) == 1
        assert poll_for(receiver, 2.0) == [payload]


def test_tag_payloads_arrive_unchanged():
    endpoint = free_endpoint()
    sent = [{"type": "tag_visual", "name": "my flask", "bbox": None, "t": T},
            {"type": "tag_text", "name": "red mug", "prompt": "red mug", "t": T},
            {"type": "untag", "name": "my flask", "t": T}]
    with CommandReceiver(endpoint) as receiver, CommandPublisher(endpoint) as pub:
        for payload in sent:
            assert pub.publish(payload, wait_s=3.0) == 1
        assert poll_for(receiver, 2.0, count=3) == sent


def test_every_subscriber_is_counted_and_one_leaving_is_noticed():
    endpoint = free_endpoint()
    with CommandPublisher(endpoint) as pub, CommandReceiver(endpoint) as a:
        b = CommandReceiver(endpoint)
        deadline = time.monotonic() + 3.0
        while pub.subscribers < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pub.subscribers == 2
        b.close()
        deadline = time.monotonic() + 3.0
        while pub.subscribers > 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pub.subscribers == 1
        assert pub.publish({"type": "clear_target", "t": T}, wait_s=1.0) == 1
        assert poll_for(a, 2.0) == [{"type": "clear_target", "t": T}]


def test_nothing_is_sent_when_nobody_subscribes():
    with CommandPublisher(free_endpoint()) as pub:
        started = time.monotonic()
        assert pub.publish({"type": "clear_target", "t": T}, wait_s=0.3) == 0
        assert 0.25 <= time.monotonic() - started < 2.0


def test_a_port_held_by_someone_else_is_reported_as_busy():
    ctx = zmq.Context()
    holder = ctx.socket(zmq.PUB)
    try:
        holder.bind("tcp://127.0.0.1:0")
        with pytest.raises(EndpointBusy):
            CommandPublisher(holder.last_endpoint.decode())
    finally:
        holder.close(linger=0)
        ctx.term()


# --- the session and the stdin loop, with fake streams ---

class FakePublisher:
    endpoint = "tcp://127.0.0.1:0"

    def __init__(self, subscribers=1):
        self.sent, self.subscribers = [], subscribers

    def publish(self, payload, wait_s):
        if self.subscribers:
            self.sent.append(payload)
        return self.subscribers


def session(tags=lambda: [], publisher=None, rules=RULES):
    out = io.StringIO()
    return VoiceSession(CLASSES, rules, publisher or FakePublisher(), tags, out, deliver_wait_s=0.1), out


def test_the_session_prints_what_it_understood_and_sent():
    s, out = session()
    assert s.handle("paul find the bottel") == SENT
    text = out.getvalue()
    assert "> paul find the bottel" in text and "understood: select target 'bottle'" in text
    assert 'sent to 1 subscriber: {"type":"select_target","label":"bottle","t":' in text
    assert s.publisher.sent[0]["label"] == "bottle" and isinstance(s.publisher.sent[0]["t"], float)


def test_the_session_says_when_a_command_went_nowhere():
    s, out = session(publisher=FakePublisher(subscribers=0))
    assert s.handle("paul phase carry") == NOT_DELIVERED
    assert "NOT DELIVERED" in out.getvalue() and "never queued" in out.getvalue()


def test_the_session_refuses_and_ignores_without_sending():
    s, out = session()
    assert s.handle("paul find the mug") == REFUSED
    assert s.handle("find the cup") == IGNORED
    assert s.publisher.sent == []
    assert "nothing sent: no known label like 'the mug'" in out.getvalue()
    assert "ignored: no wake word" in out.getvalue()


def test_tags_are_read_again_for_every_utterance():
    library = []
    s, _ = session(tags=lambda: list(library))
    assert s.handle("paul find my flask") == REFUSED
    library.append("my flask")
    assert s.handle("paul find my flask") == SENT
    assert s.publisher.sent[-1]["label"] == "my flask"


def test_tags_are_not_read_for_stray_speech():
    reads = []
    s, _ = session(tags=lambda: reads.append(1) or [])
    s.handle("find the cup")
    assert reads == []


def test_an_unreadable_library_warns_once_and_classes_still_work():
    def broken():
        raise ValueError("library.json is not valid JSON")
    s, out = session(tags=broken)
    assert s.handle("paul find the cup") == SENT
    assert s.handle("paul find the box") == SENT
    assert out.getvalue().count("warning: the label library cannot be read") == 1


def test_the_stdin_loop_handles_each_line_and_skips_blank_ones():
    s, out = session()
    lines = io.StringIO("paul find the cup\n\n   \npaul any object\r\nhello there\npaul phase carry")
    assert voice.run_lines(s, lines) == 0
    assert [p["type"] for p in s.publisher.sent] == ["select_target", "clear_target", "set_phase"]
    assert out.getvalue().count("> ") == 4


# --- the command line, on a config copy with free ports ---

def write_config(tmp_path, endpoint, deliver_wait_s=2.0, wake_words=("paul",)):
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    raw["transport"]["command_sub"] = [endpoint]
    raw["voice"]["endpoint"] = endpoint
    raw["voice"]["deliver_wait_s"] = deliver_wait_s
    raw["voice"]["wake_words"] = list(wake_words)
    raw["labels"]["dir"] = str(tmp_path / "labels")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_main_reads_stdin_and_sends_to_a_real_subscriber(tmp_path):
    endpoint = free_endpoint()
    config = write_config(tmp_path, endpoint)
    stdin = io.StringIO("paul find the cup\nfind the box\npaul find the mug\npaul set phase to carry\n"
                        "paul this is my flask\n")
    out = io.StringIO()
    with CommandReceiver(endpoint) as receiver:
        assert voice.main(["--config", config], stdin=stdin, stdout=out) == 0
        got = poll_for(receiver, 2.0, count=3)
    assert [{k: v for k, v in p.items() if k != "t"} for p in got] == [
        select("cup"), phase("carry"), tag_visual("my flask")]
    text = out.getvalue()
    assert f"bound {endpoint}" in text and "dropped, not queued" in text
    assert "ignored: no wake word" in text and "nothing sent: no known label like 'the mug'" in text


def test_main_without_wake_word_takes_every_line(tmp_path):
    endpoint = free_endpoint()
    out = io.StringIO()
    with CommandReceiver(endpoint) as receiver:
        assert voice.main(["--config", write_config(tmp_path, endpoint), "--no-wake-word"],
                          stdin=io.StringIO("find the box\n"), stdout=out) == 0
        assert [p["label"] for p in poll_for(receiver, 2.0)] == ["box"]


def test_main_reads_the_tag_library(tmp_path):
    view = View("a1b2c3", "text", 0.0, prompt="my flask")
    write_library(tmp_path / "labels", Library("w.pt", (384, 640), "views-x.npz", (Tag("my flask", (view,)),)))
    endpoint = free_endpoint()
    out = io.StringIO()
    with CommandReceiver(endpoint) as receiver:
        assert voice.main(["--config", write_config(tmp_path, endpoint), "--say", "find my flask"],
                          stdout=out) == 0
        assert [p["label"] for p in poll_for(receiver, 2.0)] == ["my flask"]
    assert "select target 'my flask' (a tag)" in out.getvalue()


def test_say_needs_no_wake_word_and_exits_0_when_sent(tmp_path):
    endpoint = free_endpoint()
    with CommandReceiver(endpoint) as receiver:
        assert voice.main(["--config", write_config(tmp_path, endpoint), "--say", "phase grasp"],
                          stdout=io.StringIO()) == 0
        assert [p["phase"] for p in poll_for(receiver, 2.0)] == ["grasp"]


def test_say_exits_1_when_nobody_listens(tmp_path):
    out = io.StringIO()
    started = time.monotonic()
    assert voice.main(["--config", write_config(tmp_path, free_endpoint(), deliver_wait_s=0.3), "--say",
                       "find the cup"], stdout=out) == 1
    assert time.monotonic() - started < 3.0
    assert "NOT DELIVERED" in out.getvalue()


def test_say_exits_1_when_refused(tmp_path):
    out = io.StringIO()
    assert voice.main(["--config", write_config(tmp_path, free_endpoint(), deliver_wait_s=0.3), "--say",
                       "find the elephant"], stdout=out) == 1
    assert "nothing sent" in out.getvalue()


def test_main_exits_1_when_the_endpoint_is_taken(tmp_path, capsys):
    ctx = zmq.Context()
    holder = ctx.socket(zmq.PUB)
    try:
        holder.bind("tcp://127.0.0.1:0")
        config = write_config(tmp_path, holder.last_endpoint.decode())
        assert voice.main(["--config", config, "--say", "find the cup"], stdout=io.StringIO()) == 1
    finally:
        holder.close(linger=0)
        ctx.term()
    assert "in use by another process" in capsys.readouterr().err


def test_main_exits_2_on_a_bad_config(tmp_path, capsys):
    path = tmp_path / "config.yaml"
    path.write_text("voice: {}\n", encoding="utf-8")
    assert voice.main(["--config", str(path), "--say", "find the cup"], stdout=io.StringIO()) == 2
    assert capsys.readouterr().err.startswith("voice: configuration:")


def test_grammar_lists_every_command(tmp_path):
    out = io.StringIO()
    assert voice.main(["--config", write_config(tmp_path, free_endpoint()), "--grammar"], stdout=out) == 0
    text = out.getvalue()
    for kind in ("select_target", "clear_target", "set_phase", "tag_visual", "tag_text", "untag"):
        assert kind in text
    assert "wake words" in text


# --- --listen, with a fake recognizer in place of Windows' ---

class FakeRecognizer:
    """Pushes what a recognizer would print, then stops as a crashed one would."""
    script: list = []
    specs: list = []

    def __init__(self, spec, events):
        self.spec, self.events, self.proc, self.started = spec, events, object(), time.monotonic()
        FakeRecognizer.specs.append(spec)

    def start(self):
        for item in FakeRecognizer.script:
            self.events.put(("recognizer", self.proc, item))
        self.events.put(("recognizer_ended", self.proc, {"code": 3, "stderr": "fake recognizer done"}))

    def stop(self):
        pass


def listen_cfg(tmp_path, endpoint, min_confidence=0.7, labels_refresh_s=2.0, dictated_names=False,
               decoy_words=()):
    cfg = load_config(write_config(tmp_path, endpoint))
    listen = dataclasses.replace(cfg.voice.listen, min_confidence=min_confidence, labels_refresh_s=labels_refresh_s,
                                 dictated_names=dictated_names, decoy_words=list(decoy_words))
    return dataclasses.replace(cfg, voice=dataclasses.replace(cfg.voice, listen=listen))


def test_listen_sends_confident_recognitions_only(tmp_path):
    FakeRecognizer.script = [{"ready_ms": 120},
                             {"text": "paul find the cup", "confidence": 0.93},
                             {"text": "paul find the box", "confidence": 0.41},
                             {"error": "something odd"}]
    FakeRecognizer.specs = []
    cfg = listen_cfg(tmp_path, free_endpoint())
    s, out = session()
    assert voice.run_listen(s, cfg, io.StringIO(""), recognizer=FakeRecognizer) == 1
    assert [p["label"] for p in s.publisher.sent] == ["cup"]
    text = out.getvalue()
    assert "confidence 0.93" in text and "below voice.listen.min_confidence" in text
    assert "listening" in text and "fake recognizer done" in text
    assert FakeRecognizer.specs[0]["wake"] == ["paul"]
    assert FakeRecognizer.specs[0]["decoy"] is cfg.voice.listen.decoy
    assert FakeRecognizer.specs[0]["adaptation"] is cfg.voice.listen.adaptation


def test_listen_never_sends_what_the_decoy_heard(tmp_path):
    # Free dictation beside the grammar: a command-shaped phrase heard there is still not sent.
    FakeRecognizer.script = [{"text": "paul find the cup", "confidence": 0.99, "grammar": "decoy"},
                             {"text": "paul find the box", "confidence": 0.95, "grammar": "commands"}]
    FakeRecognizer.specs = []
    cfg = listen_cfg(tmp_path, free_endpoint())
    s, out = session()
    assert voice.run_listen(s, cfg, io.StringIO(""), recognizer=FakeRecognizer) == 1
    assert [p["label"] for p in s.publisher.sent] == ["box"]
    assert "heard outside the commands" in out.getvalue()


def test_listen_passes_dictated_names_to_the_grammar(tmp_path):
    FakeRecognizer.script, FakeRecognizer.specs = [], []
    cfg = listen_cfg(tmp_path, free_endpoint(), dictated_names=True)
    s, _ = session()
    voice.run_listen(s, cfg, io.StringIO(""), recognizer=FakeRecognizer)
    assert any(slot == "<dictation>" for rule in FakeRecognizer.specs[0]["rules"] for slot in rule)


def test_listen_passes_decoy_words_to_the_grammar_and_sends_nothing_for_them(tmp_path):
    FakeRecognizer.script = [{"text": "paula find the cup", "confidence": 0.99, "grammar": "commands"},
                             {"text": "paul find the bowl", "confidence": 0.99, "grammar": "commands"},
                             {"text": "paul find the cup", "confidence": 0.99, "grammar": "commands"}]
    FakeRecognizer.specs = []
    cfg = listen_cfg(tmp_path, free_endpoint(), decoy_words=["bowl", "paula"])
    s, out = session()
    assert voice.run_listen(s, cfg, io.StringIO(""), recognizer=FakeRecognizer) == 1
    assert FakeRecognizer.specs[0]["wake"] == ["paul", "bowl", "paula"]
    assert FakeRecognizer.specs[0]["rules"][0][2][-2:] == ["bowl", "paula"]
    assert [p["label"] for p in s.publisher.sent] == ["cup"]
    text = out.getvalue()
    assert "ignored: no wake word" in text and "no known label like 'the bowl'" in text


def test_listen_rebuilds_the_grammar_when_the_tags_change(tmp_path):
    class SlowRecognizer(FakeRecognizer):
        def start(self):
            if len(FakeRecognizer.specs) > 1:
                super().start()  # the rebuilt one ends the test

    FakeRecognizer.script, FakeRecognizer.specs = [], []
    cfg = listen_cfg(tmp_path, free_endpoint(), labels_refresh_s=0.05)
    library = []
    s, out = session(tags=lambda: list(library))
    timer = threading.Timer(0.2, lambda: library.append("my flask"))
    timer.start()
    assert voice.run_listen(s, cfg, io.StringIO(""), recognizer=SlowRecognizer) == 1
    assert len(FakeRecognizer.specs) == 2
    assert "my flask" in FakeRecognizer.specs[1]["rules"][0][2]
    assert "grammar rebuilt" in out.getvalue()


def test_listen_says_so_when_the_recognizer_cannot_start(tmp_path):
    class MissingRecognizer(FakeRecognizer):
        def start(self):
            raise FileNotFoundError("powershell.exe not found")

    FakeRecognizer.script, FakeRecognizer.specs = [], []
    cfg = listen_cfg(tmp_path, free_endpoint())
    s, out = session()
    assert voice.run_listen(s, cfg, io.StringIO("paul phase carry\n"), recognizer=MissingRecognizer) == 1
    assert s.publisher.sent == []
    assert "cannot start the stand-in recognizer" in out.getvalue()


def test_listen_also_takes_typed_lines(tmp_path):
    class QuietRecognizer(FakeRecognizer):
        def start(self):  # hears nothing, and stops after a while so that the test ends
            threading.Timer(0.5, lambda: self.events.put(("recognizer_ended", self.proc,
                                                          {"code": 0, "stderr": ""}))).start()

    cfg = listen_cfg(tmp_path, free_endpoint())
    s, _ = session()
    assert voice.run_listen(s, cfg, io.StringIO("paul phase carry\n"), recognizer=QuietRecognizer) == 1
    assert [p["phase"] for p in s.publisher.sent] == ["carry"]


# --- the config section ---

def listen_part(**changes):
    base = dict(culture="en-US", min_confidence=0.7, labels_refresh_s=2.0, decoy=True, adaptation=False,
                dictated_names=False, decoy_words=[])
    base.update(changes)
    return VoiceListenConfig(**base)


def voice_cfg(**changes):
    base = dict(endpoint="tcp://127.0.0.1:5559", wake_words=["paul", "hey paul"], label_cutoff=0.8,
                label_margin=0.1, max_utterance_chars=200, deliver_wait_s=2.0, listen=listen_part())
    base.update(changes)
    return VoiceConfig(**base)


SUBS = ["tcp://127.0.0.1:5557", "tcp://127.0.0.1:5559"]


def test_a_valid_voice_section_passes():
    validate_voice_config(voice_cfg(), SUBS)
    validate_voice_config(voice_cfg(wake_words=[]), SUBS)  # every utterance counts
    validate_voice_config(voice_cfg(listen=listen_part(decoy_words=["bowl", "paula", "big cap"])), SUBS)


@pytest.mark.parametrize("changes, message", [
    (dict(endpoint="tcp://127.0.0.1:5561"), "transport.command_sub"),
    (dict(wake_words=["Paul"]), "lower case"),
    (dict(wake_words=["hey  paul"]), "single spaces"),
    (dict(wake_words=[""]), "non-empty"),
    (dict(wake_words=["paul", "paul"]), "duplicates"),
    (dict(label_cutoff=0.0), "label_cutoff"),
    (dict(label_cutoff=1.1), "label_cutoff"),
    (dict(label_margin=-0.1), "label_margin"),
    (dict(label_margin=1.0), "label_margin"),
    (dict(max_utterance_chars=0), "max_utterance_chars"),
    (dict(deliver_wait_s=-1.0), "deliver_wait_s"),
    (dict(listen=listen_part(culture=" ")), "culture"),
    (dict(listen=listen_part(min_confidence=1.5)), "min_confidence"),
    (dict(listen=listen_part(min_confidence=-0.1)), "min_confidence"),
    (dict(listen=listen_part(labels_refresh_s=0.0)), "labels_refresh_s"),
    (dict(listen=listen_part(decoy_words=["Paula"])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=["big  bowl"])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=[" bowl"])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=[""])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=["cup2"])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=["cap."])), "decoy_words must be lower-case words"),
    (dict(listen=listen_part(decoy_words=["bowl", "bowl"])), "decoy_words has duplicates"),
    (dict(listen=listen_part(decoy_words=["hey paul"])), "decoy_words must not hold a wake word"),
])
def test_a_bad_voice_section_is_refused(changes, message):
    with pytest.raises(ConfigError, match=message):
        validate_voice_config(voice_cfg(**changes), SUBS)


def test_the_shipped_voice_endpoint_is_one_vision_connects_to(shipped_cfg):
    assert shipped_cfg.voice.endpoint in shipped_cfg.transport.command_sub
    assert shipped_cfg.voice.endpoint != shipped_cfg.transport.command_sub[-1]  # send_command's default


def test_the_voice_section_is_checked_when_loading():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    raw["voice"]["endpoint"] = "tcp://127.0.0.1:5599"
    with pytest.raises(ConfigError, match="voice.endpoint"):
        from_dict(raw)
    del raw["voice"]["label_margin"]
    with pytest.raises(ConfigError):
        from_dict(raw)


def test_the_listen_switches_must_be_true_or_false():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    shipped = from_dict(raw).voice.listen
    assert (shipped.decoy, shipped.adaptation, shipped.dictated_names) == (True, False, False)  # as measured
    for key in ("decoy", "adaptation", "dictated_names"):
        bad = yaml.safe_load(yaml.safe_dump(raw))
        bad["voice"]["listen"][key] = "yes"
        with pytest.raises(ConfigError, match=key):
            from_dict(bad)


def test_the_shipped_decoy_words_are_the_measured_ones_and_must_be_a_list():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert from_dict(raw).voice.listen.decoy_words == ["bowl", "paula"]  # as measured by loopback
    for value in ("bowl", [1], None):
        bad = yaml.safe_load(yaml.safe_dump(raw))
        bad["voice"]["listen"]["decoy_words"] = value
        with pytest.raises(ConfigError, match="decoy_words"):
            from_dict(bad)
    del raw["voice"]["listen"]["decoy_words"]
    with pytest.raises(ConfigError, match="decoy_words"):
        from_dict(raw)
