"""Difficulty-clamp tests (Task 3, item 4): bounds at 1 and 5, ±1 movement between.

The ±1 movement and the clamp are separate concerns: difficulty_delta is model-capped to
-1..1 (AnswerScore), and the loop clamps `current + delta`. These exercise
`_clamp_difficulty(current + delta)`, matching the call site in main.py.
"""

from __future__ import annotations

import pytest

from academic_defense_simulator.main import _clamp_difficulty


@pytest.mark.parametrize(
    "value,expected",
    [
        (-3, 1),
        (0, 1),
        (1, 1),
        (3, 3),
        (5, 5),
        (6, 5),
        (99, 5),
    ],
)
def test_clamp_bounds(value, expected):
    assert _clamp_difficulty(value) == expected


@pytest.mark.parametrize(
    "current,delta,expected",
    [
        (1, -1, 1),  # floor holds against a -1
        (1, 1, 2),
        (3, 1, 4),
        (3, -1, 2),
        (5, 1, 5),  # ceiling holds against a +1
        (5, 0, 5),
        (4, 1, 5),
        (2, -1, 1),
    ],
)
def test_plus_minus_one_movement_clamped(current, delta, expected):
    assert _clamp_difficulty(current + delta) == expected
