"""KIS 실전 읽기 전용 어댑터 — 네트워크 없이 MockTransport 로 검증. `.env` 로더 포함."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import KisCredentials, load_kis_credentials
from auto_trading.core.errors import ConfigError, ExchangeError, RateLimitError
from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache
from auto_trading.exchange.overseas import TradingDisabledError

KEY, SECRET, TOKEN = "APPKEY-abcdef", "APPSECRET-xyz", "TOKEN-123"
ENV = {"KIS_APP_KEY": KEY, "KIS_APP_SECRET": SECRET, "KIS_ACCOUNT_NO": "12345678-01"}
NOW = 1_790_000_000_000


def creds(enabled: bool = False) -> KisCredentials:
    return KisCredentials(KEY, SECRET, "12345678", "01", enabled)


def make(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> KisOverseasReadOnly:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return KisOverseasReadOnly(
        creds(**kw),
        KisTokenCache(tmp_path / "kis_token.json"),
        client=client,
        clock=lambda: NOW,
        sleep=lambda _s: None,
    )


def ok(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"rt_cd": "0", "msg_cd": "MCA00000", **body})


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 86400})


class TestCredentials:
    def test_loads_and_splits_account(self, tmp_path: Path) -> None:
        c = load_kis_credentials(
            tmp_path / "none.env", environ={**ENV, "KIS_TRADING_ENABLED": "false"}
        )
        assert (c.cano, c.product_code, c.trading_enabled) == ("12345678", "01", False)

    def test_missing_names_are_listed_without_values(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as exc:
            load_kis_credentials(tmp_path / "none.env", environ={"KIS_APP_KEY": KEY})
        assert "KIS_APP_SECRET" in str(exc.value) and "KIS_ACCOUNT_NO" in str(exc.value)
        assert KEY not in str(exc.value)

    def test_bad_account_number(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError):
            load_kis_credentials(tmp_path / "x", environ={**ENV, "KIS_ACCOUNT_NO": "1234"})

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("true", True), ("TRUE", True), ("1", False), ("yes", False), ("", False)],
    )
    def test_trading_flag_is_true_only_for_true(
        self, tmp_path: Path, value: str, expected: bool
    ) -> None:
        env = {**ENV, "KIS_TRADING_ENABLED": value}
        assert load_kis_credentials(tmp_path / "x", environ=env).trading_enabled is expected

    def test_repr_hides_secrets(self) -> None:
        text = repr(creds())
        assert KEY not in text and SECRET not in text and "12345678" not in text


class TestToken:
    def test_token_is_cached_across_instances(self, tmp_path: Path) -> None:
        issued = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                issued.append(json.loads(request.content))
                return token_response()
            assert request.headers["authorization"] == f"Bearer {TOKEN}"
            assert request.headers["appkey"] == KEY and request.headers["tr_id"] == "HHDFS00000300"
            return ok({"output": {"last": "700.5", "base": "699"}})

        make(tmp_path, handler).quote("SPY")
        make(tmp_path, handler).quote("QQQ")
        assert len(issued) == 1
        assert issued[0]["grant_type"] == "client_credentials"
        cached = json.loads((tmp_path / "kis_token.json").read_text(encoding="utf-8"))
        assert KEY not in json.dumps(cached)  # 키는 지문만 남긴다

    def test_other_key_or_near_expiry_is_not_reused(self, tmp_path: Path) -> None:
        cache = KisTokenCache(tmp_path / "t.json")
        cache.save(KEY, TOKEN, NOW + 86_400_000)
        assert cache.load(KEY, NOW) == TOKEN
        assert cache.load("OTHER", NOW) is None
        cache.save(KEY, TOKEN, NOW + 5 * 60_000)  # 10분 여유 안
        assert cache.load(KEY, NOW) is None

    def test_expired_token_is_reissued_once(self, tmp_path: Path) -> None:
        calls = {"token": 0, "price": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                calls["token"] += 1
                return token_response()
            calls["price"] += 1
            if calls["price"] == 1:
                return httpx.Response(
                    500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token"}
                )
            return ok({"output": {"last": "1", "base": "1"}})

        make(tmp_path, handler).quote("SPY")
        assert calls == {"token": 2, "price": 2}

    def test_token_too_soon_is_rate_limit(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403, json={"error_code": "EGW00133", "error_description": "1분당 1회"}
            )

        with pytest.raises(RateLimitError):
            make(tmp_path, handler).quote("SPY")


class TestRequests:
    def test_rate_limit_is_retried_then_raised(self, tmp_path: Path) -> None:
        hits = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            hits.append(1)
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당"})

        with pytest.raises(RateLimitError):
            make(tmp_path, handler).quote("SPY")
        assert len(hits) == 3

    def test_network_error_retries(self, tmp_path: Path) -> None:
        hits = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            hits.append(1)
            if len(hits) < 3:
                raise httpx.ConnectError("down")
            return ok({"output": {"last": "5", "base": ""}})

        q = make(tmp_path, handler).quote("GLD")
        assert (q.exchange, str(q.last), q.prev_close) == ("AMS", "5", None)

    def test_error_message_has_no_secrets(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            return httpx.Response(500, json={"rt_cd": "1", "msg_cd": "OPSQ0001", "msg1": "오류"})

        with pytest.raises(ExchangeError) as exc:
            make(tmp_path, handler).quote("SPY")
        text = str(exc.value)
        assert "OPSQ0001" in text
        assert all(s not in text for s in (KEY, SECRET, TOKEN))

    def test_unknown_symbol_needs_exchange_and_empty_quote_fails(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            return ok({"output": {"last": "", "base": ""}})

        broker = make(tmp_path, handler)
        with pytest.raises(ExchangeError, match="거래소 코드"):
            broker.quote("TSLA")
        with pytest.raises(ExchangeError, match="시세 없음"):
            broker.quote("TSLA", exchange="NAS")


def _row(day: str, close: str = "10", low: str = "9") -> dict[str, str]:
    return {
        "xymd": day,
        "open": "10",
        "high": "11",
        "low": low,
        "clos": close,
        "tvol": "5",
        "tamt": "50",
    }


class TestDaily:
    def test_history_pages_backwards_and_stops_at_since(self, tmp_path: Path) -> None:
        pages = {
            "": [
                _row("20260925"),
                _row("20260924"),
                _row("20260923", low="12"),
            ],  # 마지막은 OHLC 위반
            "20260922": [_row("20260922", low="12")],  # 전부 불량인 페이지도 넘어간다
            "20260921": [_row("20260921"), _row("20260920")],
        }
        asked = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            bymd = request.url.params["BYMD"]
            asked.append(bymd)
            assert request.url.params["MODP"] == "1" and request.url.params["GUBN"] == "0"
            return ok({"output2": pages.get(bymd, [])})

        broker = make(tmp_path, handler)
        bars = broker.daily_history("SPY", since="2026-09-21")
        assert asked == ["", "20260922", "20260921"]
        assert len(bars) == 3  # 09-21·09-24·09-25 (09-20 은 since 밖, 09-22·09-23 은 불량)
        assert bars[0].open_ms < bars[-1].open_ms
        assert broker.skipped_rows == 2

    def test_closed_daily_respects_as_of(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            if request.url.params["BYMD"] == "20260925":
                return ok({"output2": [_row("20260925"), _row("20260924"), _row("20260923")]})
            return ok({"output2": []})

        as_of = 1_790_380_799_999  # 2026-09-25 23:59:59.999 UTC
        bars = make(tmp_path, handler).closed_daily("QQQ", as_of - 1, limit=5)
        assert len(bars) == 2  # 09-25 봉은 아직 끝나지 않았다
        assert all(b.close_ms <= as_of - 1 for b in bars)


class TestBalanceAndOrders:
    def test_balance_merges_exchanges(self, tmp_path: Path) -> None:
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth2/tokenP":
                return token_response()
            p = request.url.params
            assert request.headers["tr_id"] == "TTTS3012R"
            assert (p["CANO"], p["ACNT_PRDT_CD"]) == ("12345678", "01")
            seen.append(p["OVRS_EXCG_CD"])
            rows = [
                {
                    "ovrs_pdno": "SPY",
                    "ovrs_cblc_qty": "2",
                    "pchs_avg_pric": "700",
                    "now_pric2": "770",
                    "ovrs_stck_evlu_amt": "1540",
                    "ovrs_excg_cd": "AMEX",
                }
            ]
            return ok({"output1": rows if p["OVRS_EXCG_CD"] == "AMEX" else []})

        bal = make(tmp_path, handler).overseas_balance()
        assert seen == ["NASD", "NYSE", "AMEX"]
        assert [p.symbol for p in bal.positions] == ["SPY"]
        assert str(bal.value) == "1540"

    @pytest.mark.parametrize("enabled", [False, True])
    def test_orders_are_always_blocked(self, tmp_path: Path, enabled: bool) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("주문 차단은 네트워크에 닿지 않아야 한다")

        with pytest.raises(TradingDisabledError):
            make(tmp_path, handler, enabled=enabled).place_order("SPY", "BUY", 1)
