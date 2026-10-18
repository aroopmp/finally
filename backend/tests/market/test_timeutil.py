import math

import pytest

from app.market.timeutil import to_unix_seconds


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1617901342969834000, 1617901342.969834),  # ns (last trade)
        (1602705600000, 1602705600.0),  # ms (aggregate)
        (1602705600, 1602705600.0),  # already seconds
    ],
)
def test_units(raw, expected):
    assert to_unix_seconds(raw) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [None, 0, -5, math.nan, math.inf, True])
def test_invalid(bad):
    assert to_unix_seconds(bad) is None
