"""Validate offline design examples, not runtime authorization for hardware."""

import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def strict_json(text: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Non-finite JSON number: {value}")

    value = json.loads(text, object_pairs_hook=unique, parse_constant=reject_constant)

    def finite(node):
        if isinstance(node, float) and not math.isfinite(node):
            raise ValueError("Non-finite JSON number")
        if isinstance(node, dict):
            for child in node.values():
                finite(child)
        elif isinstance(node, list):
            for child in node:
                finite(child)

    finite(value)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def object_schema(properties):
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def constant(value):
    # JSON Schema's numeric equality must not allow True for the integer 1.
    types = {bool: "boolean", int: "integer", str: "string", type(None): "null"}
    return {"type": types[type(value)], "const": value}


def number(nullable=False, minimum=0, maximum=None, integer=False):
    kind = "integer" if integer else "number"
    schema = {"type": [kind, "null"] if nullable else kind, "minimum": minimum}
    if maximum is not None:
        schema["maximum"] = maximum
    return schema


NULL = constant(None)
FALSE = constant(False)
PROJECT_SCHEMA = object_schema(
    {
        "schema_version": constant(1),
        "status": constant("design_example_not_runtime"),
        "hardware_enabled": FALSE,
        "input": object_schema(
            {
                "kind": constant("video"),
                "path": NULL,
                "camera_index": NULL,
                "requested_width": number(minimum=1, integer=True),
                "requested_height": number(minimum=1, integer=True),
                "requested_fps": number(minimum=1),
                "max_queued_frames": constant(1),
            }
        ),
        "vision": object_schema(
            {
                "model_path": NULL,
                "model_family": constant("lightweight_yolo_custom"),
                "model_version": NULL,
                "input_size": number(minimum=1, integer=True),
                "tracker": constant("botsort"),
                "reid_enabled": FALSE,
                "confidence_threshold": number(nullable=True, maximum=1),
            }
        ),
        "tracking": object_schema(
            {
                "max_targets": constant(1),
                "acquire_frames": number(minimum=1, integer=True),
                "acquire_window_s": number(minimum=0.001),
                "short_loss_s": number(minimum=0.001),
                "reset_after_loss_s": number(minimum=0.001),
                "search_mode": constant("hold_view"),
                "move_on_predicted_only": FALSE,
            }
        ),
        "control": object_schema(
            {
                "kind": constant("P_with_dead_zone"),
                "aim_x_ratio": number(maximum=1),
                "aim_y_ratio": number(maximum=1),
                "dead_zone_ratio": number(nullable=True, maximum=1),
                "kp_pan": number(nullable=True),
                "kp_tilt": number(nullable=True),
                "kd_pan": number(),
                "kd_tilt": number(),
                "max_frame_age_ms": number(minimum=1),
                "max_rate_deg_s": number(nullable=True, minimum=0.001),
            }
        ),
        "serial": object_schema(
            {
                "enabled": FALSE,
                "port": NULL,
                "baud_rate": constant(115200),
                "max_command_hz": number(minimum=1, maximum=20),
                "watchdog_ms": constant(500),
                "protocol_version": constant(1),
                "require_explicit_arm": constant(True),
            }
        ),
        "evaluation_targets": object_schema(
            {
                "processing_fps_min": number(minimum=1),
                "host_latency_p95_ms_max": number(minimum=1),
                "center_tolerance_width_ratio": number(maximum=1),
                "center_tolerance_height_ratio": number(maximum=1),
                "center_hold_ratio_min": number(maximum=1),
                "settling_exclusion_s": number(),
            }
        ),
        "optional_indicator": object_schema(
            {"included_in_mvp": FALSE, "automatic_laser_output": FALSE}
        ),
    }
)


def axis(channel):
    return object_schema(
        {
            "channel": constant(channel),
            **dict.fromkeys(
                [
                    "min_cd",
                    "neutral_cd",
                    "max_cd",
                    "pulse_min_us",
                    "pulse_neutral_us",
                    "pulse_max_us",
                    "direction_sign",
                    "max_rate_deg_s",
                ],
                NULL,
            ),
        }
    )


CALIBRATION_SCHEMA = object_schema(
    {
        "schema_version": constant(1),
        "completed": FALSE,
        "measured_at": NULL,
        "operator": NULL,
        "pwm_hz": NULL,
        "pan": axis(0),
        "tilt": axis(1),
        "mechanical_check_passed": FALSE,
        "power_check_passed": FALSE,
    }
)
AUTOMATION_SCHEMA = object_schema(
    {
        "schema_version": constant(1),
        "stage": constant("bounded_local_fix_validation"),
        "review_backend": constant("local_chatgpt_subscription"),
        "paid_api_enabled": FALSE,
        "external_review_enabled": FALSE,
        "auto_fix_enabled": {"type": "boolean"},
        "auto_merge_enabled": FALSE,
        "max_auto_fix_attempts": constant(2),
    }
)


def validate_examples(project, calibration):
    Draft202012Validator(PROJECT_SCHEMA).validate(project)
    Draft202012Validator(CALIBRATION_SCHEMA).validate(calibration)
    if project["tracking"]["reset_after_loss_s"] <= project["tracking"]["short_loss_s"]:
        raise ValueError("reset_after_loss_s must exceed short_loss_s")
    if project["control"]["max_frame_age_ms"] >= project["serial"]["watchdog_ms"]:
        raise ValueError("frame age limit must be below watchdog")


def main():
    validate_examples(
        strict_json((ROOT / "config/project.example.json").read_text(encoding="utf-8")),
        strict_json((ROOT / "config/calibration.example.json").read_text(encoding="utf-8")),
    )
    Draft202012Validator(AUTOMATION_SCHEMA).validate(
        strict_json((ROOT / "config/automation.json").read_text(encoding="utf-8"))
    )
    print("PASS: offline examples and bounded local automation configuration")


if __name__ == "__main__":
    main()
