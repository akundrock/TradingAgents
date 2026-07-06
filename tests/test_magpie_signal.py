import pytest
import datetime as dt

from tradingagents.agents.trader.magpie import (
    _resolve_intraday_window,
    _parse_intraday_csv,
    build_default_magpie_signal,
    build_magpie_factor_inputs_from_intraday,
    create_magpie_signal_node,
    render_magpie_signal_summary,
    score_magpie_factors,
)
from tradingagents.dataflows.config import set_config
from tradingagents.default_config import DEFAULT_CONFIG


@pytest.mark.unit
def test_magpie_node_returns_disabled_signal_by_default():
    cfg = dict(DEFAULT_CONFIG, magpie_enabled=False)
    set_config(cfg)
    node = create_magpie_signal_node()
    out = node({})
    assert out["magpie_signal"]["status"] == "disabled"
    assert out["magpie_signal"]["direction"] == "Unavailable"


@pytest.mark.unit
def test_magpie_node_returns_unavailable_signal_when_enabled():
    cfg = dict(DEFAULT_CONFIG, magpie_enabled=True)
    set_config(cfg)
    node = create_magpie_signal_node()
    out = node({})
    assert out["magpie_signal"]["status"] == "unavailable"
    assert "$ADD" in render_magpie_signal_summary(out["magpie_signal"])


@pytest.mark.unit
def test_build_default_magpie_signal_has_factor_placeholders_when_enabled():
    signal = build_default_magpie_signal(enabled=True)
    assert signal["status"] == "unavailable"
    assert len(signal["factors"]) == 7


@pytest.mark.unit
def test_score_magpie_factors_emits_buy_for_fresh_long_confluence():
    signal = score_magpie_factors(
        {
            "momentum": True,
            "vwap": True,
            "hammer_pattern": True,
            "$ADD": True,
        },
        min_confirmations=3,
        transition_only=True,
        require_direction_flip=True,
        previous_direction=0,
    )
    assert signal["direction"] == "Buy"
    assert signal["long_score"] == 4
    assert signal["short_score"] == 0
    assert signal["fresh_long"] is True
    assert signal["confidence"] == "Marginal"


@pytest.mark.unit
def test_score_magpie_factors_suppresses_repeat_signal_when_transition_only():
    signal = score_magpie_factors(
        {
            "momentum": True,
            "vwap": True,
            "implied_move": True,
            "hammer_pattern": True,
        },
        min_confirmations=3,
        transition_only=True,
        previous_long_entry=True,
    )
    assert signal["long_entry"] is True
    assert signal["fresh_long"] is False
    assert signal["direction"] == "Hold"


@pytest.mark.unit
def test_score_magpie_factors_emits_sell_for_fresh_short_confluence():
    signal = score_magpie_factors(
        {
            "momentum_short": True,
            "vwap_short": True,
            "implied_move": True,
            "inverse_hammer_pattern": True,
            "$ADD_short": True,
            "$TICK_short": True,
        },
        min_confirmations=3,
        transition_only=True,
        require_direction_flip=True,
        previous_direction=0,
    )
    assert signal["direction"] == "Sell"
    assert signal["short_score"] == 6
    assert signal["confidence"] == "Premium"
    assert signal["fresh_short"] is True


@pytest.mark.unit
def test_score_magpie_factors_blocks_direction_when_flip_required():
    signal = score_magpie_factors(
        {
            "momentum": True,
            "vwap": True,
            "implied_move": True,
        },
        min_confirmations=3,
        transition_only=True,
        require_direction_flip=True,
        previous_direction=1,
    )
    assert signal["long_entry"] is True
    assert signal["fresh_long"] is False
    assert signal["direction"] == "Hold"


@pytest.mark.unit
def test_magpie_node_uses_supplied_factor_inputs_when_enabled():
    cfg = dict(DEFAULT_CONFIG, magpie_enabled=True)
    set_config(cfg)
    node = create_magpie_signal_node()
    out = node(
        {
            "magpie_factor_inputs": {
                "momentum": True,
                "vwap": True,
                "implied_move": True,
            }
        }
    )
    assert out["magpie_signal"]["status"] == "computed"
    assert out["magpie_signal"]["direction"] == "Buy"


@pytest.mark.unit
def test_parse_intraday_csv_ignores_comment_headers():
    payload = (
        "# Intraday stock data for TEST\n"
        "Date,Open,High,Low,Close,Volume\n"
        "2026-07-01 09:30:00,100,101,99,100.5,1000\n"
        "2026-07-01 09:35:00,100.5,101.5,100,101,1200\n"
    )
    df = _parse_intraday_csv(payload)
    assert len(df) == 2
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]


@pytest.mark.unit
def test_build_magpie_factor_inputs_from_intraday_produces_boolean_flags():
    base = dt.datetime(2026, 7, 1, 9, 30)
    rows = [
        {
            "Date": (base + dt.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M:%S"),
            "Open": 100 + i * 0.1,
            "High": 100.5 + i * 0.1,
            "Low": 99.8 + i * 0.1,
            "Close": 100.2 + i * 0.1,
            "Volume": 1000 + i * 10,
        }
        for i in range(12)
    ]
    df = _parse_intraday_csv(
        "Date,Open,High,Low,Close,Volume\n"
        + "\n".join(
            f"{r['Date']},{r['Open']},{r['High']},{r['Low']},{r['Close']},{r['Volume']}" for r in rows
        )
    )
    factors = build_magpie_factor_inputs_from_intraday(df)
    assert factors is not None
    required = {
        "momentum",
        "vwap",
        "implied_move",
        "hammer_pattern",
        "$ADD",
        "$TICK",
        "$VOLD",
        "momentum_short",
        "vwap_short",
        "inverse_hammer_pattern",
        "$ADD_short",
        "$TICK_short",
        "$VOLD_short",
    }
    assert required.issubset(set(factors.keys()))
    assert all(isinstance(v, bool) for v in factors.values())


@pytest.mark.unit
def test_magpie_node_fetches_intraday_when_inputs_absent(monkeypatch):
    cfg = dict(DEFAULT_CONFIG, magpie_enabled=True)
    set_config(cfg)

    def fake_route(method, *args):
        if method == "get_intraday_stock_data":
            return (
                "# Intraday stock data\n"
                "Date,Open,High,Low,Close,Volume\n"
                "2026-07-01 09:30:00,100,101,99,100.5,1000\n"
                "2026-07-01 09:35:00,100.5,101.5,100,101,1200\n"
                "2026-07-01 09:40:00,101,102,100.8,101.7,1300\n"
                "2026-07-01 09:45:00,101.7,102.1,101.5,101.8,1100\n"
                "2026-07-01 09:50:00,101.8,102.2,101.6,102,1250\n"
                "2026-07-01 09:55:00,102,102.4,101.9,102.2,1270\n"
                "2026-07-01 10:00:00,102.2,102.5,102,102.3,1320\n"
                "2026-07-01 10:05:00,102.3,102.6,102.1,102.4,1400\n"
                "2026-07-01 10:10:00,102.4,102.8,102.2,102.6,1380\n"
                "2026-07-01 10:15:00,102.6,103,102.4,102.8,1450\n"
                "2026-07-01 10:20:00,102.8,103.2,102.7,103,1480\n"
                "2026-07-01 10:25:00,103,103.4,102.9,103.1,1500\n"
            )
        if method == "get_implied_move_data":
            return (
                "# Implied move data\n"
                "Date,Symbol,UnderlyingPrice,ImpliedVolatility,DTE,Mean,Upper,Lower,Expiry\n"
                "2026-07-01 10:25:00,AAPL,103.1,0.2,3,103.0,106.0,100.0,2026-07-05\n"
            )
        if method == "get_market_internals":
            return (
                "# Market internals\n"
                "Date,$ADD,$TICK,$VOLD\n"
                "2026-07-01 10:25:00,300,1000,100000\n"
            )
        raise AssertionError(f"unexpected route method: {method}")

    monkeypatch.setattr("tradingagents.agents.trader.magpie.route_to_vendor", fake_route)
    node = create_magpie_signal_node()
    out = node({"company_of_interest": "AAPL", "trade_date": "2026-07-01"})
    assert out["magpie_signal"]["status"] == "computed"
    assert out["magpie_signal"]["direction"] in {"Buy", "Hold", "Sell"}
    implied = [f for f in out["magpie_signal"]["factors"] if f["name"] == "implied_move"][0]
    assert implied["signal"] == "bullish"


@pytest.mark.unit
def test_resolve_intraday_window_rth_intraday_caps_end_at_now():
    start_dt, end_dt, reason = _resolve_intraday_window(
        "2026-07-06",
        lookback_minutes=390,
        session_mode="rth",
        timezone_name="America/New_York",
        now=dt.datetime(2026, 7, 6, 16, 0, tzinfo=dt.timezone.utc),
    )
    assert reason == ""
    assert start_dt is not None
    assert end_dt is not None
    assert end_dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-06 12:00:00"
    assert start_dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-06 09:30:00"


@pytest.mark.unit
def test_resolve_intraday_window_rth_historical_uses_regular_session_close():
    start_dt, end_dt, reason = _resolve_intraday_window(
        "2026-07-01",
        lookback_minutes=390,
        session_mode="rth",
        timezone_name="America/New_York",
        now=dt.datetime(2026, 7, 6, 12, 0, tzinfo=dt.timezone.utc),
    )
    assert reason == ""
    assert start_dt is not None
    assert end_dt is not None
    assert start_dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-01 09:30:00"
    assert end_dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-01 16:00:00"


@pytest.mark.unit
def test_resolve_intraday_window_rth_preopen_returns_outside_session():
    start_dt, end_dt, reason = _resolve_intraday_window(
        "2026-07-06",
        lookback_minutes=390,
        session_mode="rth",
        timezone_name="America/New_York",
        now=dt.datetime(2026, 7, 6, 12, 30, tzinfo=dt.timezone.utc),
    )
    assert start_dt is None
    assert end_dt is None
    assert reason.startswith("outside_session:")


@pytest.mark.unit
def test_resolve_intraday_window_rejects_future_date():
    start_dt, end_dt, reason = _resolve_intraday_window(
        "2026-07-07",
        lookback_minutes=390,
        session_mode="rth",
        timezone_name="America/New_York",
        now=dt.datetime(2026, 7, 6, 17, 0, tzinfo=dt.timezone.utc),
    )
    assert start_dt is None
    assert end_dt is None
    assert reason.startswith("future_date:")