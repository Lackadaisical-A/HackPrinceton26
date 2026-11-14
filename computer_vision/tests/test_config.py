import copy
from pathlib import Path

import pytest
import yaml

from paul_vision.config import DEFAULT_CONFIG_PATH, ConfigError, from_dict, load_config


@pytest.fixture
def raw():
    with open(DEFAULT_CONFIG_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_shipped_config_loads(shipped_cfg):
    assert shipped_cfg.transport.vision_pub.startswith("tcp://")
    assert "default" in shipped_cfg.distance.thresholds
    assert shipped_cfg.detector.classes


def test_the_shipped_camera_stream_stays_on_this_computer(shipped_cfg):
    # Any other address would show the camera to the network; that must be a deliberate edit.
    assert shipped_cfg.stream.host == "127.0.0.1"
    assert shipped_cfg.transport.vision_pub.startswith("tcp://127.0.0.1:")


def test_models_dir_is_resolved_next_to_the_config_file(shipped_cfg):
    models_dir = Path(shipped_cfg.detector.models_dir)
    assert models_dir.is_absolute()
    assert models_dir.parent == DEFAULT_CONFIG_PATH.parent


def test_unknown_key_is_an_error(raw):
    raw["guidance"]["deadzone"] = 0.1
    with pytest.raises(ConfigError, match=r"config\.guidance: unknown key\(s\) \['deadzone'\]"):
        from_dict(raw)


def test_missing_key_is_an_error(raw):
    del raw["selection"]["coast_s"]
    with pytest.raises(ConfigError, match=r"config\.selection: missing key\(s\) \['coast_s'\]"):
        from_dict(raw)


def test_missing_section_is_an_error(raw):
    del raw["watchdog"]
    with pytest.raises(ConfigError, match="watchdog"):
        from_dict(raw)


@pytest.mark.parametrize("section, key, bad", [
    ("camera", "width", "wide"),
    ("camera", "flip_h", 1),
    ("guidance", "deadzone_enter", "small"),
    ("detector", "classes", "cup"),
    ("detector", "imgsz", 640),
    ("selection", "preferred_label", 3),
    ("stream", "enabled", "yes"),
    ("stream", "port", "8766"),
    ("stream", "max_fps", "fast"),
])
def test_wrong_type_is_an_error(raw, section, key, bad):
    raw[section][key] = bad
    with pytest.raises(ConfigError, match=f"config.{section}.{key}"):
        from_dict(raw)


def test_int_is_accepted_where_a_float_is_expected(raw):
    raw["tracker"]["ttl_s"] = 2
    assert from_dict(raw).tracker.ttl_s == 2.0


def test_optional_and_union_fields(raw):
    raw["selection"]["preferred_label"] = "cup"
    raw["detector"]["device"] = 0
    raw["source"]["default"] = 1
    cfg = from_dict(raw)
    assert (cfg.selection.preferred_label, cfg.detector.device, cfg.source.default) == ("cup", 0, 1)


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["camera"].update(rotate_deg=45), "rotate_deg"),
    (lambda r: r["camera"].update(reopen_after_s=0), "camera.reopen_after_s"),
    (lambda r: r["camera"].update(first_frame_s=0.5), "camera.first_frame_s"),
    (lambda r: r["selection"].update(conf_keep=0.9), "conf_keep"),
    (lambda r: r["guidance"].update(deadzone_exit=0.01), "deadzone"),
    (lambda r: r["distance"]["thresholds"].pop("default"), "default"),
    (lambda r: r["distance"]["thresholds"]["default"].update(near=0.5), "near < in_reach"),
    (lambda r: r["source"].update(video_end="rewind"), "video_end"),
    (lambda r: r["detector"].update(backend="tensorrt"), "detector.backend"),
    (lambda r: r["detector"].update(imgsz=[360, 640]), "detector.imgsz"),
    (lambda r: r["detector"].update(imgsz=[640]), "detector.imgsz"),
    (lambda r: r["detector"].update(classes=["cup", "cup"]), "duplicates"),
    (lambda r: r["stream"].update(host=" "), "stream.host"),
    (lambda r: r["stream"].update(port=70000), "stream.port"),
    (lambda r: r["stream"].update(port=-1), "stream.port"),
    (lambda r: r["stream"].update(max_width=0), "stream.max_width"),
    (lambda r: r["stream"].update(jpeg_quality=0), "stream.jpeg_quality"),
    (lambda r: r["stream"].update(jpeg_quality=101), "stream.jpeg_quality"),
    (lambda r: r["stream"].update(max_fps=0), "stream.max_fps"),
])
def test_cross_field_validation(raw, mutate, message):
    broken = copy.deepcopy(raw)
    mutate(broken)
    with pytest.raises(ConfigError, match=message):
        from_dict(broken)


def test_load_config_from_another_path(tmp_path, raw):
    raw["detector"]["models_dir"] = "weights"
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert Path(load_config(path).detector.models_dir) == tmp_path / "weights"
