"""Altair 실거래 실행기 — 스위치 · 입금 단위 · 낙폭 정지 (거래소 호출 없음)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ops import user_settings
from scripts import altair_demo as core
from scripts import altair_live as m


def test_off_switch_never_touches_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("꺼져 있으면 거래소에 연결하지 않는다")

    monkeypatch.setattr(m, "_client", boom)
    monkeypatch.setattr(m, "_enabled", lambda: (False, "설정 live.altair 꺼짐"))
    assert m.tick(1_792_000_000_000) == ["꺼짐 — 설정 live.altair 꺼짐"]


def test_enabled_needs_setting_and_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(m, "REPO", tmp_path)
    (tmp_path / ".env").write_text(
        "BINANCE_FUTURES_LIVE_API_KEY=k\nBINANCE_FUTURES_LIVE_API_SECRET=s\n", encoding="utf-8"
    )
    for k in (
        "BINANCE_FUTURES_LIVE_TRADING_ENABLED",
        "BINANCE_LIVE_API_KEY",
        "BINANCE_LIVE_API_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(user_settings, "get", lambda root, key: False)
    assert m._enabled() == (False, "설정 live.altair 꺼짐")
    monkeypatch.setattr(user_settings, "get", lambda root, key: True)
    assert m._enabled() == (False, "BINANCE_FUTURES_LIVE_TRADING_ENABLED 꺼짐")
    monkeypatch.setenv("BINANCE_FUTURES_LIVE_TRADING_ENABLED", "true")
    assert m._enabled() == (True, "")


def test_deposits_are_units_not_profit() -> None:
    st: dict[str, Any] = {}
    nav, dd, msgs = m.update_nav(st, 2_000.0, [(1, 2_000.0, "a")])
    assert (nav, dd, st["units"]) == (1.0, 0.0, 2_000.0)  # 첫 입금은 시작 장부
    assert msgs == ["💵 선물 지갑 입금 2,000.00 USDC — 손익 아님"]
    nav, dd, _ = m.update_nav(st, 2_200.0, [(1, 2_000.0, "a")])  # 같은 이체 다시 → 무시
    assert nav == pytest.approx(1.1) and dd == 0.0
    nav, dd, msgs = m.update_nav(st, 3_300.0, [(2, 1_100.0, "b")])  # +1,100 입금
    assert nav == pytest.approx(1.1) and st["units"] == pytest.approx(3_000.0)
    nav, dd, _ = m.update_nav(st, 2_750.0, [(3, -550.0, "c")])  # 출금 550 — 가치 그대로
    assert nav == pytest.approx(1.1) and st["units"] == pytest.approx(2_500.0)


def test_drawdown_30_percent_halts_new_exposure_only_once() -> None:
    st: dict[str, Any] = {}
    m.update_nav(st, 1_000.0, [])
    _nav, dd, msgs = m.update_nav(st, 710.0, [])
    assert dd == pytest.approx(0.29) and not msgs and m.stop_reason(st, None) is None
    _nav, dd, msgs = m.update_nav(st, 690.0, [])
    assert "낙폭 31%" in msgs[0] and st["dd_halt"]
    assert m.stop_reason(st, None) == "계좌 낙폭 31% (30% 정지)"
    _, _, again = m.update_nav(st, 650.0, [])
    assert again == []  # 알림은 한 번
    assert m.stop_reason({}, "사용자 정지") == "전체 정지 중 (사용자 정지)"


def test_unhalt_resets_peak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    sent: list[str] = []
    monkeypatch.setattr(m, "_notify", sent.append)
    assert m.unhalt() == "낙폭 정지 아님"
    m._save({"dd_halt": "계좌 낙폭 31%", "nav_peak": 1.0, "history": [[1, 690.0, 0.69]]})
    assert m.unhalt().startswith("풀림")
    st = m._load()
    assert "dd_halt" not in st and st["nav_peak"] == 0.69 and "풀림" in sent[0]


class _Filters:
    min_notional = 20
    step_size = Decimal("0.001")
    tick_size = Decimal("0.1")


class _Pos:
    def __init__(self, q: float) -> None:
        self.quantity = Decimal(str(q))
        self.entry_price = Decimal("80000")


class _Acct:
    def __init__(self) -> None:
        self.positions: dict[str, _Pos] = {}


class FakeLive:
    """롱 목표가 커져도 정지 중이면 주문하지 않는지 보는 최소 가짜."""

    def __init__(self) -> None:
        self.orders: list[Any] = []

    def asset_balance(self, asset: str) -> Decimal:
        return Decimal("1000")

    def account(self) -> _Acct:
        return _Acct()

    def filters(self, sym: str) -> _Filters:
        return _Filters()

    def mark_price(self, sym: str) -> Decimal:
        return Decimal("80000")

    def place_limit_ioc(self, *a: Any, **k: Any) -> Any:
        self.orders.append(a)
        raise AssertionError("정지 중엔 롱을 늘리지 않는다")


def test_live_equity_is_real_wallet_and_halt_blocks_long_increase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "long_targets", lambda now: ({"BTC": 0.1, "ETH": 0.1}, {}))
    c = FakeLive()
    st: dict[str, Any] = {"live": True, "no_new": "계좌 낙폭 31% (30% 정지)"}
    eq, pos = core._equity(c, st)  # type: ignore[arg-type]
    assert eq == 1000.0 and pos == {} and "wallet0" not in st
    alert: list[str] = []
    lines = core.rebalance_long(c, st, 1_792_000_000_000, dry=True, alert=alert)  # type: ignore[arg-type]
    assert any("롱 늘리기" in x and "30% 정지" in x for x in lines) and not c.orders
    assert alert and alert[0].startswith("⛔ BTC 롱 늘리기 안 함")


def test_multi_asset_mode_required_and_used_for_equity(monkeypatch: pytest.MonkeyPatch) -> None:
    class Setup:
        def __init__(self, multi: bool) -> None:
            self.multi = multi
            self.lev: list[str] = []

        def dual_side_position(self) -> bool:
            return False

        def multi_assets_margin(self) -> bool:
            return self.multi

        def set_leverage(self, sym: str, lev: int) -> None:
            self.lev.append(sym)

    st: dict[str, Any] = {}
    single = Setup(False)
    assert "다중 자산 모드" in (m._setup(single, st, 1) or "")  # type: ignore[arg-type]
    assert not single.lev and not st.get("setup")
    multi = Setup(True)
    assert m._setup(multi, st, 1) is None and st["setup"]  # type: ignore[arg-type]

    class Live(FakeLive):
        def margin_balance(self) -> Decimal:
            return Decimal("1234.5")  # USDT + USDC + 미실현

    eq, _pos = core._equity(Live(), {"live": True, "multi": True})  # type: ignore[arg-type]
    assert eq == 1234.5
