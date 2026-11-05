import pytest

from tools.pan_tilt import AxisConfig, PanTiltController

SHAPE = (100, 200)
LOWER_RIGHT = (145, 70, 155, 80)
UPPER_LEFT = (45, 20, 55, 30)
CENTER = (90, 45, 110, 55)


def observe(controller, at, box=LOWER_RIGHT, *, shape=SHAPE, now=None):
    return controller.update(at, shape, box, now=at if now is None else now)


@pytest.mark.parametrize("box, expected", [(LOWER_RIGHT, 91.5), (UPPER_LEFT, 88.5)])
def test_original_coordinates_and_axis_direction(box, expected):
    controller = PanTiltController(
        AxisConfig(60, 90, 120, 1, 60, 40), AxisConfig(70, 90, 110, -1, 60, 40)
    )
    assert observe(controller, 0, box).state == "HOLD"
    command = observe(controller, 0.05, box)
    assert command.state == "TRACK"
    assert (command.pan_deg, command.tilt_deg) == pytest.approx((expected, 180 - expected))
    assert controller.transport.latest == command


def test_dead_zone_holds_current_target_and_speed_uses_elapsed_time():
    controller = PanTiltController.simulated()
    edge = (190, 90, 200, 100)
    observe(controller, 0, edge)
    first = observe(controller, 0.02, edge)
    second = observe(controller, 0.07, edge)
    centered = observe(controller, 0.09, (96, 47, 110, 53))
    assert (first.pan_deg, first.tilt_deg) == pytest.approx((90.6, 90.6))
    assert (second.pan_deg, second.tilt_deg) == pytest.approx((92.1, 92.1))
    assert centered.state == "TRACK"
    assert (centered.pan_deg, centered.tilt_deg) == pytest.approx((92.1, 92.1))


@pytest.mark.parametrize("direction, boundary", [(1, 91), (-1, 89)])
def test_range_saturates_without_accumulation_and_allows_immediate_inward_step(direction, boundary):
    controller = PanTiltController(
        AxisConfig(89, 90, 91, direction, 60, 30), AxisConfig(89, 90, 91, direction, 60, 30)
    )
    observe(controller, 0)
    for index in range(1, 11):
        command = observe(controller, index * 0.05)
        assert command.state == "TRACK" and command.saturated
        assert (command.pan_deg, command.tilt_deg) == (boundary, boundary)
    inward = observe(controller, 0.55, UPPER_LEFT)
    assert not inward.saturated
    assert (inward.pan_deg, inward.tilt_deg) == pytest.approx((90 - 0.5 * direction,) * 2)


def test_loss_holds_last_command_and_recovery_does_not_integrate_over_gap():
    controller = PanTiltController.simulated()
    observe(controller, 0)
    moved = observe(controller, 0.05)
    lost = observe(controller, 0.08, None)
    assert lost.state == "HOLD" and not lost.saturated
    assert (lost.pan_deg, lost.tilt_deg) == (moved.pan_deg, moved.tilt_deg)
    assert observe(controller, 2.0) == lost
    resumed = observe(controller, 2.05)
    assert resumed.state == "TRACK"
    assert resumed.pan_deg == pytest.approx(moved.pan_deg + 1.5)


@pytest.mark.parametrize(
    "at, now, shape, box",
    [
        (0.08, 0.181, SHAPE, LOWER_RIGHT),  # Older than the control cutoff.
        (0.08, 0.07, SHAPE, LOWER_RIGHT),  # Future observation.
        (0.05, 0.06, SHAPE, LOWER_RIGHT),  # Duplicate frame.
        (0.04, 0.06, SHAPE, LOWER_RIGHT),  # Out of order.
        (0.08, 0.08, (120, 200), LOWER_RIGHT),  # Input geometry changed.
        (0.08, 0.08, SHAPE, (0, 0, float("nan"), 30)),
        (0.08, 0.08, SHAPE, (0, 0, 201, 30)),
    ],
)
def test_unusable_observations_hold_without_moving(at, now, shape, box):
    controller = PanTiltController.simulated()
    observe(controller, 0)
    moved = observe(controller, 0.05)
    result = observe(controller, at, box, shape=shape, now=now)
    assert result.state == "HOLD"
    assert (result.pan_deg, result.tilt_deg) == (moved.pan_deg, moved.tilt_deg)


def test_live_expiration_holds_without_an_incoming_frame():
    controller = PanTiltController.simulated()
    observe(controller, 0)
    moved = observe(controller, 0.05)
    assert controller.snapshot(0.12) == moved
    held = controller.snapshot(0.16)
    assert held.state == "HOLD" and controller.transport.latest == held
    assert controller.transport.latest.pan_deg == moved.pan_deg
    assert observe(controller, 0.17, CENTER).state == "HOLD"


def test_fresh_result_after_expiration_does_not_integrate_across_gap():
    controller = PanTiltController.simulated()
    observe(controller, 0)
    moved = observe(controller, 0.05)
    # The new box is fresh on arrival, but the previous observation has expired.
    held = observe(controller, 0.10, now=0.17)
    assert held.state == "HOLD" and held.pan_deg == moved.pan_deg
    assert observe(controller, 0.18).pan_deg == pytest.approx(moved.pan_deg + 2.4)
