"""나무 모의투자 클라이언트 — 가짜 서버 (실제 호출 없음)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import NamuCredentials
from auto_trading.exchange.namu import (
    LIVE_URL,
    MOCK_URL,
    NamuError,
    NamuMock,
    NamuTokenCache,
)

CREDS = NamuCredentials("k", "s")


def _client(tmp_path: Path, handler: Any) -> tuple[NamuMock, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrap(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return handler(req)  # type: ignore[no-any-return]

    cache = NamuTokenCache(tmp_path / "tok.json")
    cache.save("cached", 2e9)
    nh = NamuMock(
        CREDS,
        cache,
        client=httpx.Client(transport=httpx.MockTransport(wrap)),
        clock=lambda: 1.0e9,
        sleep=lambda s: None,
    )
    return nh, seen


def _accounts(req: httpx.Request) -> httpx.Response | None:
    if req.url.path == "/n2/acctinfo":
        rows = [{"acct_no": "11111111101", "acct_type": "01"}]
        rows.append({"acct_no": "22222222203", "acct_type": "03"})
        return httpx.Response(200, json={"Output_0": rows})
    return None


def test_only_mock_url_and_mock_account(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        NamuMock(CREDS, NamuTokenCache(tmp_path / "t.json"), base_url=LIVE_URL)

    def handler(req: httpx.Request) -> httpx.Response:
        acc = _accounts(req)
        if acc is not None:
            return acc
        body = json.loads(req.content)["Input_0"]
        assert req.url.host == "moapi.nhplug.com" and body["act_no"] == "22222222203"
        assert body["nmn_pr_tp_cd"] == "01"  # 지정가
        return httpx.Response(200, json={"Output_0": {"mkt_orr_no": 77}})

    nh, seen = _client(tmp_path, handler)
    assert nh.limit("buy", "005940", 3, 23_300) == 77
    assert seen[0].headers["Authorization"] == "Bearer cached"  # 저장된 토큰 재사용
    assert all(r.url.path != "/oauth2/token" for r in seen)
    with pytest.raises(ValueError):
        nh.limit("buy", "005940", 0, 23_300)


def test_rate_limit_waits_without_new_token_and_errors_carry_message(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        acc = _accounts(req)
        if acc is not None:
            return acc
        if req.url.path.endswith("cashSell"):
            msg = {"usr_msg": "주문가능수량이 부족합니다", "msg_code": "E1"}
            return httpx.Response(200, json={"message": msg})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"message": {"usr_msg": "초과"}})
        return httpx.Response(200, json={"Output_0": [{"itg_orr_no": 5, "orr_qty": 1}]})

    nh, seen = _client(tmp_path, handler)
    assert [o.order_no for o in nh.orders("20261015")] == [5]
    assert all(r.url.path != "/oauth2/token" for r in seen)
    with pytest.raises(NamuError, match="주문가능수량"):
        nh.limit("sell", "005940", 1, 23_250)


def test_quote_reads_live_domain(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.nhplug.com"  # 모의투자는 시세를 주지 않는다
        return httpx.Response(200, json={"Output_0": {"stck_prpr": 100, "askp1": 101}})

    nh, _ = _client(tmp_path, handler)
    assert nh.quote("005940")["askp1"] == 101
    assert MOCK_URL.startswith("https://moapi.")
