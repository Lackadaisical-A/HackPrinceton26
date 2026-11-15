"""A config.yaml that cannot be read: load_config says which file and where, and the command-line tools that read
it (voice, send_command) exit 2 with one line and no traceback. Nothing here binds a port: the config fails first."""
import io
import subprocess
import sys
from pathlib import Path

import pytest

from paul_vision import send_command, voice
from paul_vision.config import ConfigError, load_config

PROJECT = Path(__file__).resolve().parents[1]

BROKEN = [  # (file content, what the message must say)
    ("voice: [unclosed\n  x: 1\n", "expected ',' or ']', but got ':' at line 2, column 4"),
    ("voice:\n  wake_words: [paul, hey paul\n  label_cutoff: 0.9\n", "at line 3, column 15"),
    ("camera:\n  index: 0\n   width: 640\n", "mapping values are not allowed here at line 3, column 9"),
    ("voice:\n\tendpoint: x\n", "found character '\\t' that cannot start any token at line 2, column 1"),
    ('voice:\n  endpoint: "tcp://127.0.0.1:5559\n', "found unexpected end of stream at line 3, column 1"),
    ("voice:\n  endpoint: a\x01b\n", "unacceptable character #x0001: special characters are not allowed at line 2, "
                                    "column 14"),
]


@pytest.fixture
def broken_file(tmp_path):
    def write(text: str) -> Path:
        path = tmp_path / "config.yaml"
        path.write_text(text, encoding="utf-8")
        return path
    return write


@pytest.mark.parametrize("text, where", BROKEN)
def test_invalid_yaml_is_a_config_error_naming_the_file_and_the_place(broken_file, text, where):
    path = broken_file(text)
    with pytest.raises(ConfigError) as info:
        load_config(path)
    message = str(info.value)
    assert message.startswith(f"{path.resolve()} is not valid YAML: ") and message.endswith(where)
    assert "\n" not in message


def test_a_file_that_is_not_utf8_is_a_config_error(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_bytes("voice:\n  endpoint: caf\xe9\n".encode("latin-1"))
    with pytest.raises(ConfigError, match="is not UTF-8 text"):
        load_config(path)


def test_a_missing_file_is_still_an_os_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_voice_exits_2_with_one_line(broken_file, capsys):
    path = broken_file(BROKEN[0][0])
    assert voice.main(["--config", str(path), "--say", "find the cup"], stdout=io.StringIO()) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"voice: configuration: {path.resolve()} is not valid YAML: ") and "line 2, column 4" in err
    assert err.count("\n") == 1 and "Traceback" not in err


def test_send_command_exits_2_with_one_line(broken_file, capsys):
    path = broken_file(BROKEN[0][0])
    assert send_command.main(["--config", str(path), "set_phase", "carry"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"send_command: configuration: {path.resolve()} is not valid YAML: ")
    assert err.count("\n") == 1 and "Traceback" not in err


@pytest.mark.parametrize("module, args", [("paul_vision.voice", ["--say", "find the cup"]),
                                          ("paul_vision.send_command", ["set_phase", "carry"])])
def test_the_real_processes_exit_2_without_a_traceback(broken_file, module, args):
    path = broken_file(BROKEN[0][0])
    done = subprocess.run([sys.executable, "-m", module, "--config", str(path), *args], cwd=PROJECT,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 2
    assert "is not valid YAML" in done.stderr and "line 2, column 4" in done.stderr
    assert done.stderr.strip().count("\n") == 0 and "Traceback" not in done.stderr and done.stdout == ""
