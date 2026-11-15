"""The label library (M4): names, views, files. Numpy only, like the module."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from paul_vision import labels as lb
from paul_vision.labels import LabelError, Library, Tag, View


def text_view(view_id: str, prompt: str = "flask") -> View:
    return View(view_id, lb.KIND_TEXT, 1.0, prompt=prompt)


def visual_view(view_id: str) -> View:
    return View(view_id, lb.KIND_VISUAL, 1.0, image=f"refs/{view_id}.png", bbox=(294.0, 372.0, 380.0, 515.0))


def emb(seed: int, n: int = 1) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=(n, lb.EMBED_DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def saved(directory: Path, lib: Library, seed: int = 0) -> Library:
    """`lib` with a views file for its views, as the worker leaves it."""
    ids = [v.id for _, v in lib.views]
    name = lb.save_view_embeddings(directory, ids, emb(seed, len(ids)))
    return Library("yoloe-26s-seg.pt", (384, 640), name, lib.tags)


LIMITS = dict(max_tags=3, max_views=2)


# --- names ---

@pytest.mark.parametrize("raw, expected", [("My  Flask", "my flask"), ("  my\tflask ", "my flask"), ("CUP", "cup")])
def test_names_are_normalized(raw, expected):
    assert lb.normalize_name(raw) == expected


@pytest.mark.parametrize("name, reason", [
    ("", "empty"),
    ("x" * 41, "too long"),
    ("my/flask", "slash"),
    ("flask#2", "hash"),
    ("drop station", "station"),
    ("bad\x07name", "cannot be used"),
])
def test_bad_names_are_refused_with_a_sentence(name, reason):
    problem = lb.name_problem(name, max_chars=40, reserved=("pick station", "drop station"))
    assert problem is not None and reason in problem and problem.endswith(".")


def test_a_good_name_and_a_configured_class_are_allowed():
    assert lb.name_problem("my flask", max_chars=40, reserved=("drop station",)) is None
    assert lb.name_problem("x" * 40, max_chars=40) is None
    assert lb.name_problem("cup", max_chars=40) is None  # adds views to the configured class


def test_view_classes_map_back_to_their_name():
    lib = Library(tags=(Tag("my flask", (text_view("a"), text_view("b"))), Tag("cup", (text_view("c"),))))
    assert lb.class_names(["cup", "bottle"], lib) == ["cup", "bottle", "my flask#0", "my flask#1", "cup#0"]
    assert lb.public_labels(["cup", "bottle"], lib) == ["cup", "bottle", "my flask", "my flask", "cup"]
    assert [lb.label_of(c) for c in lb.class_names(["cup"], lib)] == ["cup", "my flask", "my flask", "cup"]
    assert lb.known_labels(["cup", "bottle"], lib) == ["cup", "bottle", "my flask"]  # no duplicate cup


# --- changes ---

def test_add_a_tag_then_a_view_then_remove_it():
    lib = lb.with_view(lb.EMPTY, "my flask", text_view("a"), **LIMITS)
    lib = lb.with_view(lib, "my flask", text_view("b"), **LIMITS)
    lib = lb.with_view(lib, "my mug", text_view("c"), **LIMITS)
    assert [(t.name, [v.id for v in t.views]) for t in lib.tags] == [("my flask", ["a", "b"]), ("my mug", ["c"])]
    lib = lb.without_tag(lib, "my flask")
    assert [t.name for t in lib.tags] == ["my mug"]


def test_limits():
    lib = lb.with_view(lb.EMPTY, "a", text_view("1"), **LIMITS)
    lib = lb.with_view(lib, "a", text_view("2"), **LIMITS)
    with pytest.raises(LabelError, match="already has 2 views"):
        lb.with_view(lib, "a", text_view("3"), **LIMITS)
    lib = lb.with_view(lib, "b", text_view("4"), **LIMITS)
    lib = lb.with_view(lib, "c", text_view("5"), **LIMITS)
    with pytest.raises(LabelError, match="full"):
        lb.with_view(lib, "d", text_view("6"), **LIMITS)
    with pytest.raises(LabelError, match="Nothing was learned as e"):
        lb.without_tag(lib, "e")
    with pytest.raises(ValueError, match="max_tags"):
        lb.check_limits(lib, max_tags=2, max_views=2)


# --- files ---

def test_the_library_survives_a_round_trip(tmp_path):
    lib = saved(tmp_path, Library(tags=(Tag("my flask", (visual_view("a1"), text_view("b2", "steel flask"))),)))
    lb.write_library(tmp_path, lib)
    assert lb.load_library(tmp_path) == lib
    assert lb.read_tag_names(tmp_path) == ["my flask"]
    data = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["tags"][0]["name"] == "my flask"


def test_no_library_is_an_empty_one(tmp_path):
    assert lb.load_library(tmp_path / "missing") == lb.EMPTY
    assert lb.read_tag_names(tmp_path / "missing") == []


def test_the_index_is_replaced_atomically(tmp_path, monkeypatch):
    lb.write_library(tmp_path, lb.EMPTY)
    replaced = []
    real = os.replace
    monkeypatch.setattr(lb.os, "replace", lambda a, b: replaced.append((Path(a).name, Path(b).name)) or real(a, b))
    lb.write_library(tmp_path, saved(tmp_path, Library(tags=(Tag("x", (text_view("a"),)),))))
    assert replaced[-1][1] == "library.json" and replaced[-1][0].endswith(".tmp")
    assert [p.name for p in tmp_path.glob(".*.tmp")] == []


def test_a_file_held_open_for_a_moment_is_replaced_on_a_second_try(tmp_path, monkeypatch):
    # Windows refuses to replace a file another process is reading (the voice process reads the names).
    real, calls = os.replace, []
    lib = saved(tmp_path, Library(tags=(Tag("x", (text_view("a"),)),)))

    def busy_once(a, b):
        calls.append(b)
        if len(calls) == 1:
            raise PermissionError("in use")
        real(a, b)

    monkeypatch.setattr(lb.os, "replace", busy_once)
    lb.write_library(tmp_path, lib)
    assert lb.read_tag_names(tmp_path) == ["x"] and len(calls) == 2


def test_a_staged_change_that_cannot_be_deleted_now_is_left(tmp_path, monkeypatch):
    lb.write_library(tmp_path, lb.EMPTY, lb.NEXT_FILE)

    def held(self, missing_ok=False):
        raise PermissionError("in use")

    monkeypatch.setattr(Path, "unlink", held)
    lb.discard_next(tmp_path)  # no exception: it runs on the way out


def test_a_failed_write_leaves_the_old_index(tmp_path, monkeypatch):
    lb.write_library(tmp_path, lb.EMPTY)

    def broken(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(lb.os, "replace", broken)
    with pytest.raises(OSError):
        lb.write_library(tmp_path, saved(tmp_path, Library(tags=(Tag("x", (text_view("a"),)),))))
    assert lb.read_tag_names(tmp_path) == []


@pytest.mark.parametrize("content, message", [
    ("{not json", "not valid JSON"),
    ("[]", "expected a JSON object"),
    ('{"version": 2, "tags": []}', "version 2"),
    ('{"tags": []}', "version None"),
    ('{"version": 1, "tags": [{"nom": "x"}]}', "`tags`"),
    ('{"version": 1, "tags": "x"}', "`tags`"),
])
def test_a_corrupt_or_foreign_file_is_an_error(tmp_path, content, message):
    (tmp_path / "library.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        lb.read_tag_names(tmp_path)
    with pytest.raises(ValueError, match=message):
        lb.load_library(tmp_path)


@pytest.mark.parametrize("tags, extra, message", [
    ([{"name": "x", "views": []}], {}, "no views"),
    ([{"name": "x", "views": [{"id": "a", "kind": "sound"}]}], {}, "kind"),
    ([{"name": "x", "views": [{"id": "a", "kind": "text"}]}], {}, "prompt"),
    ([{"name": "x", "views": [{"id": "a", "kind": "visual", "image": "refs/a.png"}]}], {}, "bbox"),
    ([{"name": "x", "views": [{"id": "a", "kind": "text", "prompt": "p"}]},
      {"name": "x", "views": [{"id": "b", "kind": "text", "prompt": "p"}]}], {}, "appears twice"),
    ([{"name": "x", "views": [{"id": "a", "kind": "text", "prompt": "p"}]}], {"weights": None}, "needs `weights`"),
])
def test_a_library_that_does_not_add_up_is_an_error(tmp_path, tags, extra, message):
    data = {"version": 1, "weights": "w.pt", "imgsz": [384, 640], "embeddings": "views-x.npz", "tags": tags, **extra}
    (tmp_path / "library.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        lb.load_library(tmp_path)


def test_names_can_be_read_from_a_file_with_more_keys_than_this_code_knows(tmp_path):
    data = {"version": 1, "tags": [{"name": "my flask", "colour": "steel"}], "owner": "someone"}
    (tmp_path / "library.json").write_text(json.dumps(data), encoding="utf-8")
    assert lb.read_tag_names(tmp_path) == ["my flask"]


def test_a_staged_change_goes_live_only_on_commit(tmp_path):
    live = saved(tmp_path, Library(tags=(Tag("a", (text_view("1"),)),)))
    lb.write_library(tmp_path, live)
    staged = saved(tmp_path, Library(tags=(Tag("a", (text_view("1"),)), Tag("b", (text_view("2"),)))), seed=1)
    lb.write_library(tmp_path, staged, lb.NEXT_FILE)
    assert lb.read_tag_names(tmp_path) == ["a"]
    lb.commit_next(tmp_path)
    assert lb.read_tag_names(tmp_path) == ["a", "b"] and not (tmp_path / lb.NEXT_FILE).exists()
    lb.write_library(tmp_path, live, lb.NEXT_FILE)
    lb.discard_next(tmp_path)
    lb.discard_next(tmp_path)  # nothing staged: fine
    assert lb.read_tag_names(tmp_path) == ["a", "b"]


# --- embeddings ---

def test_view_embeddings_follow_model_order_and_survive_removal(tmp_path):
    lib = Library(tags=(Tag("a", (text_view("1"), text_view("2"))), Tag("b", (text_view("3"),))))
    rows = emb(5, 3)
    lib = Library("w.pt", (384, 640), lb.save_view_embeddings(tmp_path, ["1", "2", "3"], rows), lib.tags)
    assert np.array_equal(lb.load_view_embeddings(tmp_path, lib), rows)
    smaller = lb.subset_embeddings(tmp_path, lib, lb.without_tag(lib, "a"))
    assert smaller.embeddings != lib.embeddings
    assert np.array_equal(lb.load_view_embeddings(tmp_path, smaller), rows[[2]])
    views = lb.model_views(tmp_path, smaller, export=tmp_path / "x_openvino_model")
    assert views.classes == ("b#0",) and views.labels == ("b",) and views.embeddings.shape == (1, 512)
    assert lb.subset_embeddings(tmp_path, smaller, lb.without_tag(smaller, "b")) == lb.EMPTY


def test_a_views_file_that_does_not_match_is_an_error(tmp_path):
    lib = saved(tmp_path, Library(tags=(Tag("a", (text_view("1"),)),)))
    other = Library(lib.weights, lib.imgsz, lib.embeddings, (Tag("a", (text_view("2"),)),))
    with pytest.raises(ValueError, match="does not hold"):
        lb.load_view_embeddings(tmp_path, other)
    with pytest.raises(ValueError, match="cannot read"):
        lb.load_view_embeddings(tmp_path, Library("w", (1, 1), "views-missing.npz", lib.tags))
    with pytest.raises(ValueError, match="finite"):
        lb.save_view_embeddings(tmp_path, ["1"], np.full((1, 512), np.nan, np.float32))


def test_the_export_key_changes_with_everything_that_changes_the_network(tmp_path):
    lib = saved(tmp_path, Library(tags=(Tag("a", (text_view("1"),)),)))
    key = lb.export_key(lib, "yoloe-26s-seg.pt", (384, 640), ["cup"])
    assert key == lb.export_key(lib, "models/yoloe-26s-seg.pt", [384, 640], ["cup"])
    assert key != lb.export_key(lib, "yoloe-26m-seg.pt", (384, 640), ["cup"])
    assert key != lb.export_key(lib, "yoloe-26s-seg.pt", (480, 640), ["cup"])
    assert key != lb.export_key(lib, "yoloe-26s-seg.pt", (384, 640), ["cup", "box"])
    assert key != lb.export_key(saved(tmp_path, lib, seed=9), "yoloe-26s-seg.pt", (384, 640), ["cup"])
    assert lb.export_dir(tmp_path, key).name.endswith("_openvino_model")


def test_a_library_made_for_other_weights_or_input_size_is_stale():
    lib = Library("yoloe-26s-seg.pt", (384, 640), "views-x.npz", (Tag("a", (text_view("1"),)),))
    assert lb.stale_reason(lib, "models/yoloe-26s-seg.pt", [384, 640]) is None
    assert "yoloe-26m-seg.pt" in lb.stale_reason(lib, "yoloe-26m-seg.pt", [384, 640])
    assert "[480, 640]" in lb.stale_reason(lib, "yoloe-26s-seg.pt", [480, 640])
    assert lb.stale_reason(lb.EMPTY, "other.pt", [1, 1]) is None


def test_prune_keeps_what_is_used_and_deletes_the_rest(tmp_path):
    lib = saved(tmp_path, Library(tags=(Tag("a", (visual_view("v1"),)),)))
    old = lb.save_view_embeddings(tmp_path, ["gone"], emb(3))
    (tmp_path / "refs").mkdir()
    (tmp_path / "refs" / "v1.png").write_bytes(b"png")
    (tmp_path / "refs" / "gone.png").write_bytes(b"png")
    (tmp_path / ".library.json.123.tmp").write_bytes(b"half")
    for key in ("keep", "old"):
        (tmp_path / "openvino" / f"{key}_openvino_model").mkdir(parents=True)
    removed = lb.prune(tmp_path, [lib], ["keep"])
    assert sorted(Path(p).name for p in removed) == sorted([old, "gone.png", ".library.json.123.tmp",
                                                            "old_openvino_model"])
    assert (tmp_path / "refs" / "v1.png").exists() and (tmp_path / lib.embeddings).exists()
    assert (tmp_path / "openvino" / "keep_openvino_model").exists()


def test_export_complete_needs_every_file(tmp_path):
    out = tmp_path / "k_openvino_model"
    out.mkdir()
    for name in ("metadata.yaml", "m.xml"):
        (out / name).write_text("x")
        assert not lb.export_complete(out)
    (out / "m.bin").write_bytes(b"x")
    assert lb.export_complete(out)


# --- the offline commands ---

def test_cli_lists_and_removes(tmp_path, shipped_cfg, monkeypatch, capsys):
    import dataclasses

    cfg = dataclasses.replace(shipped_cfg, labels=dataclasses.replace(shipped_cfg.labels, dir=str(tmp_path)))
    monkeypatch.setattr(lb, "load_config", lambda path: cfg)
    lb.write_library(tmp_path, saved(tmp_path, Library(tags=(Tag("my flask", (visual_view("v1"),)),
                                                             Tag("cup", (text_view("t1", "mug"),))))))
    assert lb.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "my flask: 1 view(s)" in out and "adds views to a configured class" in out
    assert lb.main(["remove", "My", "Flask"]) == 0
    assert lb.read_tag_names(tmp_path) == ["cup"]
    assert lb.main(["remove", "nothing"]) == 1
    assert "Nothing was learned as nothing." in capsys.readouterr().err
    (tmp_path / "library.json").write_text("{", encoding="utf-8")
    assert lb.main(["list"]) == 2


@pytest.mark.parametrize("content", ["detector: [cup\nlabels: {}\n",  # not YAML
                                     "detector:\n  classes: [cup]\n",  # YAML, not a config
                                     None])                             # no file
def test_cli_reports_a_config_that_does_not_load_in_one_line(tmp_path, capsys, content):
    path = tmp_path / "config.yaml"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    assert lb.main(["--config", str(path), "list"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("configuration: ") and err.count("\n") == 1
    assert not (tmp_path / "labels").exists()


def test_the_module_imports_without_torch_ultralytics_or_opencv():
    code = ("import sys; import paul_vision.labels; "
            "bad = [m for m in ('torch', 'ultralytics', 'cv2') if m in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          cwd=Path(__file__).parents[1])
    assert done.returncode == 0, done.stdout + done.stderr
