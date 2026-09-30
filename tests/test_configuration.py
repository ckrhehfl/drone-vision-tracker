import copy
from pathlib import Path

import pytest
from jsonschema import ValidationError

from tools.validate_config import strict_json, validate_examples

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def examples():
    return tuple(
        strict_json((ROOT / f"config/{name}.example.json").read_text(encoding="utf-8"))
        for name in ("project", "calibration")
    )


def test_offline_examples_are_valid(examples):
    validate_examples(*examples)


@pytest.mark.parametrize(
    "path,value",
    [
        (("hardware_enabled",), True),
        (("hardware_enabled",), "false"),
        (("serial", "enabled"), True),
        (("serial", "protocol_version"), 2),
        (("serial", "require_explicit_arm"), False),
        (("serial", "max_command_hz"), 21),
        (("input", "max_queued_frames"), 2),
        (("input", "max_queued_frames"), True),
        (("input", "requested_width"), 0),
        (("vision", "confidence_threshold"), 1.1),
        (("vision", "confidence_threshold"), -0.1),
        (("tracking", "max_targets"), 2),
        (("tracking", "move_on_predicted_only"), True),
        (("tracking", "acquire_frames"), 1.5),
        (("tracking", "reset_after_loss_s"), 0.2),
        (("control", "max_frame_age_ms"), 500),
        (("control", "max_rate_deg_s"), -1),
        (("control", "aim_y_ratio"), 2),
        (("optional_indicator", "automatic_laser_output"), True),
    ],
)
def test_invalid_or_unsafe_project_is_rejected(examples, path, value):
    project, calibration = copy.deepcopy(examples)
    node = project
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises((ValidationError, ValueError)):
        validate_examples(project, calibration)


@pytest.mark.parametrize(
    "path,value",
    [
        (("completed",), True),
        (("pan", "min_cd"), -100),
        (("tilt", "direction_sign"), 0),
        (("tilt", "channel"), 0),
        (("power_check_passed",), True),
    ],
)
def test_calibration_cannot_fabricate_measurement(examples, path, value):
    project, calibration = examples
    node = calibration
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        validate_examples(project, calibration)


def test_missing_and_unknown_fields_rejected(examples):
    project, calibration = examples
    del project["serial"]["enabled"]
    with pytest.raises(ValidationError):
        validate_examples(project, calibration)
    project["serial"]["enabled"] = False
    project["enable_hardware"] = True
    with pytest.raises(ValidationError):
        validate_examples(project, calibration)


@pytest.mark.parametrize(
    "text",
    [
        '{"x": NaN}',
        '{"x": Infinity}',
        '{"x": 1e999}',
        '{"x":1,"x":2}',
        "[]",
    ],
)
def test_nonstandard_and_ambiguous_json_rejected(text):
    with pytest.raises(ValueError):
        strict_json(text)
