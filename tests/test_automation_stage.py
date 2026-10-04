import copy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tools.validate_config import AUTOMATION_SCHEMA, strict_json


def settings():
    path = Path(__file__).resolve().parents[1] / "config/automation.json"
    return strict_json(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("enabled", [True, False])
def test_bounded_stage_allows_fixer_or_emergency_disable(enabled):
    config = settings()
    config["auto_fix_enabled"] = enabled
    Draft202012Validator(AUTOMATION_SCHEMA).validate(config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("max_auto_fix_attempts", 3),
        ("max_auto_fix_attempts", 0),
        ("auto_merge_enabled", True),
        ("paid_api_enabled", True),
        ("external_review_enabled", True),
        ("auto_fix_enabled", "true"),
        ("stage", "read_only_review_setup"),
    ],
)
def test_activation_cannot_expand_other_boundaries(key, value):
    config = copy.deepcopy(settings())
    config.update(stage="collaborative_merge_validation", auto_merge_enabled=False)
    config[key] = value
    with pytest.raises(ValidationError):
        Draft202012Validator(AUTOMATION_SCHEMA).validate(config)


@pytest.mark.parametrize("enabled", [True, False])
def test_verified_operational_stage_supports_merge_and_emergency_disable(enabled):
    config = settings()
    config.update(stage="conditional_auto_merge", auto_merge_enabled=enabled)
    Draft202012Validator(AUTOMATION_SCHEMA).validate(config)


@pytest.mark.parametrize("field", ["auto_merge_enabled", "auto_fix_enabled"])
def test_operational_stage_rejects_non_boolean_activation(field):
    config = settings()
    config[field] = "true"
    with pytest.raises(ValidationError):
        Draft202012Validator(AUTOMATION_SCHEMA).validate(config)
