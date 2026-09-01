from datetime import datetime

import pytest

from tradingagents.mes.indicators import (
    SMA,
    AverageVolume,
    LaguerreRSI,
    SessionVWAP,
    WilderATR,
    hhmm_to_tod,
    is_rth,
    parse_hhmm,
    tod,
)


@pytest.mark.unit
def test_sma_reports_not_ready_until_the_window_fills():
    sma = SMA(3)
    assert sma.update(1.0) == (0.0, False)
    assert sma.update(2.0) == (0.0, False)
    assert sma.update(3.0) == (2.0, True)
    assert sma.update(4.0) == (3.0, True)


@pytest.mark.unit
def test_sma_length_is_floored_at_one():
    sma = SMA(0)
    assert sma.update(5.0) == (5.0, True)


@pytest.mark.unit
def test_wilder_atr_seeds_with_a_simple_average_then_smooths():
    atr = WilderATR(3)
    assert atr.update(10.0, 8.0, 9.0) == (0.0, False)
    assert atr.update(11.0, 9.0, 10.0) == (0.0, False)

    value, ready = atr.update(12.0, 10.0, 11.0)
    assert ready is True
    assert value == pytest.approx(2.0)

    value, ready = atr.update(14.0, 10.0, 13.0)
    assert ready is True
    # True range 4.0 smoothed into a 2.0 seed: (2*2 + 4) / 3.
    assert value == pytest.approx(8.0 / 3.0)


@pytest.mark.unit
def test_average_volume_matches_a_simple_moving_average():
    avg = AverageVolume(2)
    assert avg.update(100.0) == (0.0, False)
    assert avg.update(200.0) == (150.0, True)


@pytest.mark.unit
def test_session_vwap_single_bar_has_zero_sigma():
    vwap = SessionVWAP()
    value, ready = vwap.update("2026-03-30", 10.0, 100.0)
    assert (value, ready) == (10.0, True)
    assert vwap.sigma() == 0.0


@pytest.mark.unit
def test_session_vwap_is_volume_weighted_with_a_positive_sigma():
    vwap = SessionVWAP()
    vwap.update("2026-03-30", 10.0, 100.0)
    value, ready = vwap.update("2026-03-30", 20.0, 100.0)
    assert (value, ready) == (15.0, True)
    assert vwap.sigma() == pytest.approx(5.0)


@pytest.mark.unit
def test_session_vwap_resets_on_a_new_session_key():
    vwap = SessionVWAP()
    vwap.update("2026-03-30", 10.0, 100.0)
    vwap.update("2026-03-30", 20.0, 100.0)

    value, ready = vwap.update("2026-03-31", 50.0, 10.0)
    assert (value, ready) == (50.0, True)
    assert vwap.sigma() == 0.0


@pytest.mark.unit
def test_session_vwap_zero_volume_bar_holds_the_previous_value():
    vwap = SessionVWAP()
    vwap.update("2026-03-30", 10.0, 100.0)
    assert vwap.update("2026-03-30", 999.0, 0.0) == (10.0, True)


@pytest.mark.unit
def test_session_vwap_zero_volume_before_any_volume_is_not_ready():
    assert SessionVWAP().update("2026-03-30", 10.0, 0.0) == (0.0, False)


@pytest.mark.unit
def test_laguerre_rsi_warms_up_then_stays_within_zero_and_one():
    rsi = LaguerreRSI(nfe=3)
    candles = [
        (10.0, 11.0, 9.5, 10.5),
        (10.5, 11.5, 10.0, 11.0),
        (11.0, 12.0, 10.5, 11.5),
        (11.5, 12.5, 11.0, 12.0),
        (12.0, 12.5, 11.0, 11.2),
        (11.2, 11.4, 10.2, 10.4),
    ]
    readiness = []
    for candle in candles:
        value, prev, ready = rsi.update(*candle)
        readiness.append(ready)
        assert 0.0 <= value <= 1.0
        assert 0.0 <= prev <= 1.0

    # First call only primes prev_close; the TR window then needs nfe more bars.
    assert readiness[:3] == [False, False, False]
    assert all(readiness[3:])


@pytest.mark.unit
def test_laguerre_rsi_nfe_is_floored_at_two():
    assert LaguerreRSI(nfe=0).nfe == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (datetime(2026, 3, 30, 0, 0), 0),
        (datetime(2026, 3, 30, 9, 30), 930),
        (datetime(2026, 3, 30, 15, 5), 1505),
        (datetime(2026, 3, 30, 23, 59), 2359),
    ],
)
def test_tod_packs_hours_and_minutes(ts, expected):
    assert tod(ts) == expected


@pytest.mark.unit
def test_parse_hhmm_and_hhmm_to_tod():
    assert parse_hhmm("09:45") == (9, 45)
    assert hhmm_to_tod("09:45") == 945
    assert hhmm_to_tod("16:00") == 1600


@pytest.mark.unit
@pytest.mark.parametrize("value", ["0945", "9:45:00", ""])
def test_parse_hhmm_rejects_malformed_values(value):
    with pytest.raises(ValueError):
        parse_hhmm(value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ts", "expected"),
    [
        (datetime(2026, 3, 30, 9, 29), False),
        (datetime(2026, 3, 30, 9, 30), True),
        (datetime(2026, 3, 30, 12, 0), True),
        (datetime(2026, 3, 30, 16, 0), True),
        (datetime(2026, 3, 30, 16, 1), False),
    ],
)
def test_is_rth_boundaries_are_inclusive(ts, expected):
    assert is_rth(ts, "09:30", "16:00") is expected
