import math

from alpha_spy.v2_lifecycle_survival import successor_authority_from_calibration


def _calibration(**overrides):
    values = {
        "transition_scored_forecasts": 120,
        "transition_accuracy": 0.68,
        "transition_skill_vs_majority": 0.10,
        "transition_log_loss": 1.00,
        "transition_uniform_log_loss": math.log(4.0),
    }
    values.update(overrides)
    return values


def test_successor_authority_requires_enough_actual_transitions():
    assert successor_authority_from_calibration(
        _calibration(transition_scored_forecasts=99)
    ) is False


def test_successor_authority_requires_skill_over_majority_baseline():
    assert successor_authority_from_calibration(
        _calibration(transition_accuracy=0.72, transition_skill_vs_majority=0.01)
    ) is False


def test_successor_authority_requires_probabilistic_skill_over_uniform():
    uniform = math.log(4.0)
    assert successor_authority_from_calibration(
        _calibration(transition_log_loss=uniform)
    ) is False


def test_successor_authority_can_turn_on_only_after_all_gates_clear():
    assert successor_authority_from_calibration(_calibration()) is True
