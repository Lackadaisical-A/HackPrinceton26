from contextlib import ExitStack
from pathlib import Path
import socket
import sys

import pytest

from leslider_control.session import (ROOT, controller_argv, ensure_service, listening, parse_args,
                                     session_config)


def test_default_matches_working_command_and_dry_run_is_forwarded():
    args = parse_args(["--dry-run"])
    assert (args.controller, args.leader_port, args.robot_port) == ("leslider", "COM9", "COM6")
    assert "--dry-run" in controller_argv(args)
    assert "--telemetry" in controller_argv(args)
    assert "--auto-enable" not in controller_argv(args)
    assert parse_args(["--controller", "pots"]).controller == "pots"


@pytest.mark.parametrize("arguments", [
    ["--leader-port", "COM6"], ["--web-port", "8765"], ["--stream-port", "0"],
    ["--", "--auto-enable"], ["--", "--robot-port=COM7"],
])
def test_reject_ambiguous_ports_or_automatic_motion(arguments):
    with pytest.raises(SystemExit):
        parse_args(arguments)


def test_generated_config_preserves_model_location_and_gpu_selection():
    path = ROOT / "computer_vision" / "config.yaml"
    before = path.read_bytes()
    cfg = session_config(path, 18766)
    assert cfg["stream"]["port"] == 18766
    assert Path(cfg["detector"]["models_dir"]).is_absolute()
    assert path.read_bytes() == before


def test_services_started_by_launcher_are_stopped_on_controller_failure(tmp_path):
    # Exercise actual child creation, readiness and ExitStack cleanup on an unused port.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(RuntimeError, match="controller failed"):
        with ExitStack() as stack:
            ensure_service(stack, "bridge", port,
                           [sys.executable, "-m", "paul_bridge", "--listen", str(port),
                            "--commands", "tcp://127.0.0.1:0"], ROOT / "bridge", tmp_path)
            assert listening(port)
            raise RuntimeError("controller failed")
    assert not listening(port)
