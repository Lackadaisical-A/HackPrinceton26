import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import zmq

from paul_vision import send_command
from paul_vision.commands import CommandReceiver
from paul_vision.config import DEFAULT_CONFIG_PATH
from paul_vision.phase import PHASES
from paul_vision.send_command import EndpointBusy, build_command, main, send

CLASSES = ["cup", "bottle", "pill bottle"]
CONFIG = str(DEFAULT_CONFIG_PATH)


def free_endpoint() -> str:
    """A port nothing listens on, found by the OS; never one of the shipped 5555 to 5560."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"tcp://127.0.0.1:{probe.getsockname()[1]}"


def poll_for(receiver: CommandReceiver, seconds: float) -> list:
    out, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline and not out:
        out.extend(receiver.poll())
        time.sleep(0.01)
    return out


# --- the payload (pure) ---

@pytest.mark.parametrize("phase", PHASES)
def test_set_phase_payload(phase):
    payload = build_command("set_phase", phase, CLASSES)
    assert payload["type"] == "set_phase" and payload["phase"] == phase and isinstance(payload["t"], float)


def test_select_and_clear_target_payloads():
    assert {k: v for k, v in build_command("select_target", "pill bottle", CLASSES).items() if k != "t"} == {
        "type": "select_target", "label": "pill bottle"}
    assert set(build_command("clear_target", None, CLASSES)) == {"t", "type"}


@pytest.mark.parametrize("kind, value, message", [
    ("set_phase", "Carry", "set_phase needs one of"),
    ("set_phase", None, "set_phase needs one of"),
    ("select_target", "mug", "detector.classes"),
    ("select_target", "drop station", "detector.classes"),
    ("select_target", None, "detector.classes"),
    ("clear_target", "cup", "takes no argument"),
    ("set_haptics", "x", "unknown command"),
    ("tag_visual", None, "needs a name"),
    ("tag_text", "  ", "needs a name"),
    ("untag", None, "needs a name"),
])
def test_a_command_vision_would_ignore_is_refused_before_sending(kind, value, message):
    with pytest.raises(ValueError, match=message):
        build_command(kind, value, CLASSES)


def test_tag_payloads():
    def strip(payload):
        return {k: v for k, v in payload.items() if k != "t"}

    assert strip(build_command("tag_visual", "my flask", CLASSES, bbox="0.23,0.52,0.30,0.72")) == {
        "type": "tag_visual", "name": "my flask", "bbox": [0.23, 0.52, 0.30, 0.72]}
    assert strip(build_command("tag_visual", "my flask", CLASSES)) == {
        "type": "tag_visual", "name": "my flask", "bbox": None}  # null: the locked target
    assert strip(build_command("tag_text", "my mug", CLASSES, prompt="red mug")) == {
        "type": "tag_text", "name": "my mug", "prompt": "red mug"}
    assert strip(build_command("tag_text", "my mug", CLASSES)) == {"type": "tag_text", "name": "my mug"}
    assert strip(build_command("untag", "my flask", CLASSES)) == {"type": "untag", "name": "my flask"}


@pytest.mark.parametrize("kind, value, extra, message", [
    ("tag_visual", "a", {"bbox": "0.1,0.2,0.3"}, "x1,y1,x2,y2"),
    ("tag_visual", "a", {"bbox": "0.3,0.2,0.1,0.4"}, "x1 < x2"),
    ("tag_visual", "a", {"bbox": "0.1,0.2,0.3,1.4"}, "0..1"),
    ("tag_visual", "a", {"bbox": "a,b,c,d"}, "x1,y1,x2,y2"),
    ("tag_text", "a", {"prompt": " "}, "must not be empty"),
    ("tag_text", "a", {"bbox": "0.1,0.1,0.2,0.2"}, "only for tag_visual"),
    ("untag", "a", {"prompt": "x"}, "only for tag_text"),
])
def test_bad_tag_arguments_are_refused(kind, value, extra, message):
    with pytest.raises(ValueError, match=message):
        build_command(kind, value, CLASSES, **extra)


def test_select_target_accepts_a_tag_name_from_the_library(tmp_path, shipped_cfg, monkeypatch, capsys):
    import dataclasses

    from paul_vision import labels as lb

    lb.write_library(tmp_path, lb.Library("w.pt", (384, 640), "views-x.npz", (lb.Tag("my flask", (
        lb.View("a1", lb.KIND_TEXT, 0.0, prompt="flask"),)),)))
    cfg = dataclasses.replace(shipped_cfg, labels=dataclasses.replace(shipped_cfg.labels, dir=str(tmp_path)))
    monkeypatch.setattr(send_command, "load_config", lambda path: cfg)
    payloads = []
    monkeypatch.setattr(send_command, "send", lambda endpoint, payload, wait_s: payloads.append(payload) or 1)
    assert main(["select_target", "my", "flask"]) == 0
    assert payloads[0]["label"] == "my flask"
    assert main(["tag_visual", "my", "flask", "--bbox", "0.23,0.52,0.30,0.72"]) == 0
    assert payloads[1]["bbox"] == [0.23, 0.52, 0.30, 0.72] and payloads[1]["name"] == "my flask"


# --- over a real socket, on a free port ---

def test_a_command_reaches_a_receiver_that_connected_first():
    endpoint = free_endpoint()
    with CommandReceiver([free_endpoint(), endpoint]) as receiver:  # as vision: several senders, one of them us
        payload = build_command("set_phase", "carry", CLASSES)
        assert send(endpoint, payload, wait_s=3.0) == 1
        assert poll_for(receiver, 2.0) == [payload]


def test_send_counts_every_subscriber():
    endpoint = free_endpoint()
    with CommandReceiver(endpoint) as a, CommandReceiver(endpoint) as b:
        payload = build_command("clear_target", None, CLASSES)
        assert send(endpoint, payload, wait_s=3.0) == 2
        assert poll_for(a, 2.0) == [payload] and poll_for(b, 2.0) == [payload]


def test_nothing_is_sent_when_nobody_subscribes():
    started = time.monotonic()
    assert send(free_endpoint(), {"type": "clear_target"}, wait_s=0.3) == 0
    assert time.monotonic() - started < 2.0


def test_a_port_held_by_someone_else_is_reported_as_busy():
    ctx = zmq.Context()
    holder = ctx.socket(zmq.PUB)
    try:
        holder.bind("tcp://127.0.0.1:0")
        with pytest.raises(EndpointBusy):
            send(holder.last_endpoint.decode(), {"type": "clear_target"}, wait_s=0.3)
    finally:
        holder.close(linger=0)
        ctx.term()


# --- the command line ---

def test_the_default_endpoint_is_the_last_command_sub_entry(shipped_cfg, monkeypatch, capsys):
    # The shipped default is in use by nothing in tests: send() is replaced, so nothing binds 5560.
    sent = []
    monkeypatch.setattr(send_command, "send", lambda endpoint, payload, wait_s: sent.append(endpoint) or 1)
    assert main(["set_phase", "carry", "--config", CONFIG]) == 0
    assert sent == [shipped_cfg.transport.command_sub[-1]]
    assert capsys.readouterr().out.startswith('sent {"t":')


def test_a_label_with_a_space_needs_no_quotes(monkeypatch, capsys):
    payloads = []
    monkeypatch.setattr(send_command, "send", lambda endpoint, payload, wait_s: payloads.append(payload) or 1)
    assert main(["select_target", "pill", "bottle", "--config", CONFIG]) == 0
    assert payloads[0]["label"] == "pill bottle"


def test_an_endpoint_vision_does_not_listen_on_is_pointed_out(monkeypatch, capsys):
    monkeypatch.setattr(send_command, "send", lambda endpoint, payload, wait_s: 1)
    assert main(["clear_target", "--endpoint", "tcp://127.0.0.1:1", "--config", CONFIG]) == 0
    assert "is not one of them" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["set_phase", "lunch"], ["select_target", "mug"], ["clear_target", "cup"],
                                  ["set_phase", "carry", "--wait", "0"]])
def test_bad_arguments_exit_2_with_one_line(argv, monkeypatch, capsys):
    monkeypatch.setattr(send_command, "send", lambda *a: pytest.fail("must not send"))
    assert main([*argv, "--config", CONFIG]) == 2
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and err.startswith("send_command: ")


def test_nobody_subscribed_exits_1_with_one_line(capsys):
    assert main(["clear_target", "--endpoint", free_endpoint(), "--wait", "0.2", "--config", CONFIG]) == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2 and "note:" in lines[0]  # this free port is not in command_sub, so a note first
    assert "nothing was sent" in lines[1]


def test_a_port_held_by_another_process_exits_1_with_one_line_naming_the_option():
    # A real second process, as when the console bridge already holds the port; the output is what a user sees.
    ctx = zmq.Context()
    holder = ctx.socket(zmq.PUB)
    try:
        holder.bind("tcp://127.0.0.1:0")
        endpoint = holder.last_endpoint.decode()
        done = subprocess.run([sys.executable, "-m", "paul_vision.send_command", "set_phase", "carry",
                               "--endpoint", endpoint, "--config", CONFIG],
                              capture_output=True, text=True, timeout=60, cwd=Path(__file__).parents[1])
    finally:
        holder.close(linger=0)
        ctx.term()
    assert done.returncode == 1
    assert "Traceback" not in done.stderr and done.stdout == ""
    reason = [line for line in done.stderr.splitlines() if "note:" not in line]
    assert len(reason) == 1
    assert "in use by another process" in reason[0] and "--endpoint" in reason[0]
