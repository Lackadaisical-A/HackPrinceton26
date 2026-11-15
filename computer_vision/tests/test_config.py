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


def test_shipped_command_senders_each_have_their_own_endpoint(shipped_cfg):
    # The console bridge, voice and send_command each bind one; send_command's default is the last.
    assert shipped_cfg.transport.command_sub == ["tcp://127.0.0.1:5557", "tcp://127.0.0.1:5559", "tcp://127.0.0.1:5564",
                                                 "tcp://127.0.0.1:5560"]


def test_shipped_stations_have_distance_thresholds(shipped_cfg):
    # Without their own entry the station markers would be judged by `default`, which is set for objects.
    for label in ("pick station", "drop station"):
        assert label in shipped_cfg.distance.thresholds


def test_a_single_command_endpoint_must_be_a_list(raw):
    raw["transport"]["command_sub"] = "tcp://127.0.0.1:5557"
    with pytest.raises(ConfigError, match="config.transport.command_sub"):
        from_dict(raw)


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
    ("guidance", "aim", "centre"),
    ("guidance", "aim", [0.5, "middle"]),
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


def test_the_shipped_aim_is_the_frame_centre(shipped_cfg):
    # The console mirrors this as AIM_DEFAULT (frontend/src/config.ts).
    assert shipped_cfg.guidance.aim == [0.5, 0.5]


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
    (lambda r: r["guidance"].update(aim=[1.2, 0.5]), r"guidance\.aim must be \[x, y\]"),
    (lambda r: r["guidance"].update(aim=[0.5, -0.1]), r"guidance\.aim must be \[x, y\]"),
    (lambda r: r["guidance"].update(aim=[0.5]), r"guidance\.aim must be \[x, y\]"),
    (lambda r: r["guidance"].update(aim=[0.5, 0.5, 0.5]), r"guidance\.aim must be \[x, y\]"),
    (lambda r: r["distance"]["thresholds"].pop("default"), "default"),
    (lambda r: r["distance"]["thresholds"]["default"].update(near=0.5), "near < in_reach"),
    (lambda r: r["distance"]["thresholds"]["drop station"].update(in_reach=0.9),
     r"distance\.thresholds\.drop station: need in_reach \* \(1 \+ distance\.hysteresis\) < 1"),
    (lambda r: (r["distance"].update(hysteresis=0.5),
                r["distance"]["thresholds"]["default"].update(near=0.1, in_reach=0.7)),
     r"distance\.thresholds\.default: need in_reach \* \(1 \+ distance\.hysteresis\) < 1"),
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
    (lambda r: r["transport"].update(command_sub=[]), "command_sub"),
    (lambda r: r["transport"].update(command_sub=["tcp://127.0.0.1:1", "tcp://127.0.0.1:1"]), "duplicates"),
    (lambda r: r["phase"].update(place_hint="close"), "phase.place_hint"),
    (lambda r: r["phase"].update(leave_place_hint="near"), "leave_place_hint must be further"),
    (lambda r: r["phase"].update(blind_spot_s=-1), "phase.blind_spot_s"),
    (lambda r: r["phase"].update(alert_hold_s=0), "phase.alert_hold_s"),
])
def test_cross_field_validation(raw, mutate, message):
    broken = copy.deepcopy(raw)
    mutate(broken)
    with pytest.raises(ConfigError, match=message):
        from_dict(broken)


def test_in_reach_must_leave_room_for_the_hysteresis_margin(raw):
    from paul_vision.guidance import classify_distance
    raw["distance"].update(hysteresis=0.25)
    raw["distance"]["thresholds"]["default"].update(near=0.1, in_reach=0.79)  # 0.79 * 1.25 = 0.9875: accepted
    accepted = from_dict(raw).distance.thresholds["default"]
    assert classify_distance(1.0, accepted, "near", 0.25) == "in_reach"
    raw["distance"]["thresholds"]["default"].update(in_reach=0.8)  # 0.8 * 1.25 = 1.0: refused
    with pytest.raises(ConfigError, match=r"distance\.thresholds\.default: need in_reach"):
        from_dict(raw)
    # What it refuses: a box that fills the whole frame never takes a target already seen to in_reach.
    refused = type(accepted)(near=0.1, in_reach=0.8)
    assert classify_distance(1.0, refused, "near", 0.25) == "near"
    assert classify_distance(1.0, refused, "far", 0.25) == "near"


def test_load_config_from_another_path(tmp_path, raw):
    raw["detector"]["models_dir"] = "weights"
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert Path(load_config(path).detector.models_dir) == tmp_path / "weights"


# --- labels: (M4, the operator's tags) ---

def test_labels_dir_is_resolved_next_to_the_config_file(tmp_path, raw, shipped_cfg):
    assert Path(shipped_cfg.labels.dir) == DEFAULT_CONFIG_PATH.parent / "labels"
    raw["labels"]["dir"] = "my_tags"
    path = tmp_path / "custom.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert Path(load_config(path).labels.dir) == tmp_path / "my_tags"


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["labels"].update(dir=" "), "labels.dir"),
    (lambda r: r["labels"].update(max_tags=0), "labels.max_tags"),
    (lambda r: r["labels"].update(max_views=0), "labels.max_views"),
    (lambda r: r["labels"].update(max_name_chars=0), "labels.max_name_chars"),
    (lambda r: r["labels"].update(max_prompt_chars=0), "labels.max_prompt_chars"),
    (lambda r: r["labels"].update(snap_iou=0), "labels.snap_iou"),
    (lambda r: r["labels"].update(snap_iou=1.5), "labels.snap_iou"),
    (lambda r: r["labels"].update(border_margin=-0.1), "labels.border_margin"),
    (lambda r: r["labels"].update(border_margin=0.3), "labels.border_margin"),
    (lambda r: r["labels"].update(min_box_side=0), "labels.min_box_side"),
    (lambda r: r["labels"].update(worker_timeout_s=0), "labels.worker_timeout_s"),
    (lambda r: r["labels"].update(worker_threads=0), "labels.worker_threads"),
    (lambda r: r["labels"].update(load_timeout_s=0), "labels.load_timeout_s"),
    (lambda r: r["detector"].update(classes=["cup", "cup#1"]), "#"),  # '#' marks a tag's views inside the model
])
def test_labels_validation(raw, mutate, message):
    broken = copy.deepcopy(raw)
    mutate(broken)
    with pytest.raises(ConfigError, match=message):
        from_dict(broken)


# --- gripper_load: contact from the gripper servo's load ---

def test_the_shipped_gripper_load_is_off_until_its_thresholds_are_measured(shipped_cfg):
    g = shipped_cfg.gripper_load
    assert g.enabled is False
    assert 0 <= g.off_threshold < g.on_threshold <= 1 and g.stale_s > 0.1


def test_gripper_load_empty_pos_takes_a_number_or_null(raw):
    raw["gripper_load"].update(empty_pos=12)
    assert from_dict(raw).gripper_load.empty_pos == 12.0
    raw["gripper_load"].update(empty_pos=None)
    assert from_dict(raw).gripper_load.empty_pos is None


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["gripper_load"].update(sign=0), "gripper_load.sign"),
    (lambda r: r["gripper_load"].update(sign=2), "gripper_load.sign"),
    (lambda r: r["gripper_load"].update(sign=1.0), r"gripper_load\.sign: expected int"),
    (lambda r: r["gripper_load"].update(off_threshold=0.3, on_threshold=0.3), "gripper_load.off_threshold"),
    (lambda r: r["gripper_load"].update(on_threshold=1.2), "gripper_load.on_threshold"),
    (lambda r: r["gripper_load"].update(off_threshold=-0.1), "gripper_load.off_threshold"),
    (lambda r: r["gripper_load"].update(on_hold_s=-0.1), "gripper_load.on_hold_s"),
    (lambda r: r["gripper_load"].update(off_hold_s=-0.1), "gripper_load.off_hold_s"),
    (lambda r: r["gripper_load"].update(stale_s=0.1), "gripper_load.stale_s"),
    (lambda r: r["gripper_load"].update(empty_pos=float("nan")), "gripper_load.empty_pos"),
    (lambda r: r["gripper_load"].update(empty_margin=-1), "gripper_load.empty_margin"),
    (lambda r: r["gripper_load"].update(enabled="yes"), "gripper_load.enabled"),
    (lambda r: r["gripper_load"].update(rate_hz=10), "unknown key"),
    (lambda r: r.pop("gripper_load"), r"missing key\(s\) \['gripper_load'\]"),
])
def test_gripper_load_validation(raw, mutate, message):
    broken = copy.deepcopy(raw)
    mutate(broken)
    with pytest.raises(ConfigError, match=message):
        from_dict(broken)
