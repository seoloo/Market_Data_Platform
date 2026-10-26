"""한국투자증권 접속 창구 시험 — 허용 목록 · 토큰 · 다시 시도 · 요청 간격 · 이어 받기.

가짜 네트워크(httpx.MockTransport)와 지어낸 응답만 쓴다(실제 네트워크 · 키 없음).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from mdp.errors import DataIntegrityError, ForbiddenRequestError, SourceUnavailableError
from mdp.kis.client import (
    ALLOWED_GET,
    CHK_HOLIDAY,
    DAILY_BARS,
    Credentials,
    KisClient,
    KisEnv,
    MissingCredentialsError,
    Quotation,
    TokenCache,
)

DEMO = Credentials(KisEnv.DEMO, "PAPERKEY-TEST", "PAPERSECRET-TEST")
REAL = Credentials(KisEnv.REAL, "REALKEY-TEST", "REALSECRET-TEST")
OK = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다.", "output": []}

Handler = Callable[[httpx.Request], httpx.Response]


class Fake:
    """가짜 원천 — 토큰 발급은 언제나 되고, 조회는 ``reply`` 가 정한다."""

    def __init__(self, reply: Handler | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.tokens_issued = 0
        self.reply = reply or (lambda request: httpx.Response(200, json=OK))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/oauth2/tokenP":
            self.tokens_issued += 1
            return httpx.Response(
                200, json={"access_token": f"TOKEN{self.tokens_issued}", "expires_in": 86400}
            )
        if request.url.path == "/oauth2/Approval":
            return httpx.Response(200, json={"approval_key": "APPROVAL1"})
        return self.reply(request)

    def queries(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]


def _client(
    fake: Fake, tmp_path: Path, credentials: Credentials = DEMO, **kwargs: Any
) -> tuple[KisClient, list[float]]:
    sleeps: list[float] = []
    client = KisClient(
        credentials,
        httpx.Client(transport=httpx.MockTransport(fake.handler)),
        token_cache=TokenCache(tmp_path / "token.json"),
        sleep=sleeps.append,
        clock=lambda: 1_000_000.0,
        wall_clock=lambda: 1_000_000.0,
        **kwargs,
    )
    return client, sleeps


# ------------------------------------------------------------------ 허용 목록


@pytest.mark.parametrize(
    ("method", "path", "tr_id"),
    [
        ("POST", "/uapi/domestic-stock/v1/trading/order-cash", "VTTC0012U"),  # 현금 주문
        ("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R"),  # 잔고
        ("GET", "/uapi/domestic-stock/v1/trading/inquire-psbl-order", "VTTC8908R"),  # 주문 가능
        ("GET", DAILY_BARS.path, "VTTC8434R"),  # 허용 경로에 다른 TR
        ("GET", DAILY_BARS.path, None),
        ("POST", DAILY_BARS.path, DAILY_BARS.tr_id),
        ("POST", "/uapi/hashkey", None),  # 주문용 해시
        ("DELETE", "/oauth2/revokeP", None),
    ],
)
def test_orders_balance_and_unknown_requests_are_refused(
    tmp_path: Path, method: str, path: str, tr_id: str | None
) -> None:
    client, _ = _client(Fake(), tmp_path)
    with pytest.raises(ForbiddenRequestError):
        client.check_allowed(method, KisEnv.DEMO.url(path), tr_id)


@pytest.mark.parametrize(
    "url",
    [
        f"https://openapi.koreainvestment.com:9443{DAILY_BARS.path}",  # 모의 창구로 실전 주소
        f"http://openapivts.koreainvestment.com:29443{DAILY_BARS.path}",
        f"https://evil.example.com:29443{DAILY_BARS.path}",
    ],
)
def test_other_hosts_are_refused(tmp_path: Path, url: str) -> None:
    client, _ = _client(Fake(), tmp_path)
    with pytest.raises(ForbiddenRequestError):
        client.check_allowed("GET", url, DAILY_BARS.tr_id)


def test_allow_list_has_no_trading_paths() -> None:
    assert all("/trading/" not in q.path for q in ALLOWED_GET)
    assert all(q.tr_id[0] in "FC" and q.tr_id[1:3] != "TT" for q in ALLOWED_GET)


def test_forbidden_quotation_never_reaches_the_network(tmp_path: Path) -> None:
    fake = Fake()
    client, _ = _client(fake, tmp_path)
    order = Quotation("/uapi/domestic-stock/v1/trading/order-cash", "VTTC0012U", "주문")
    with pytest.raises(ForbiddenRequestError):
        client.get(order, {})
    assert fake.requests == []


# ------------------------------------------------------------------ 토큰


def test_token_is_issued_once_and_reused_from_cache(tmp_path: Path) -> None:
    fake = Fake()
    client, _ = _client(fake, tmp_path)
    client.get(DAILY_BARS, {"FID_INPUT_ISCD": "005930"})
    client.get(DAILY_BARS, {"FID_INPUT_ISCD": "000660"})
    again, _ = _client(fake, tmp_path)  # 새 창구도 캐시를 쓴다
    again.get(DAILY_BARS, {"FID_INPUT_ISCD": "005930"})
    assert fake.tokens_issued == 1
    query = fake.queries()[0]
    assert query.headers["authorization"] == "Bearer TOKEN1"
    assert query.headers["tr_id"] == DAILY_BARS.tr_id
    assert query.headers["appkey"] == "PAPERKEY-TEST"
    assert query.url.host == "openapivts.koreainvestment.com" and query.url.port == 29443


def test_cache_from_another_key_is_not_used(tmp_path: Path) -> None:
    cache = TokenCache(tmp_path / "token.json")
    cache.save(DEMO, "OTHER", 9e9)
    assert cache.load(REAL, 1_000_000.0) is None
    assert cache.load(DEMO, 1_000_000.0) == "OTHER"
    assert cache.load(DEMO, 9e9 - 60) is None  # 만료 10 분 안


def test_expired_token_is_reissued_once(tmp_path: Path) -> None:
    replies = iter(
        [
            httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "만료"}),
            httpx.Response(200, json=OK),
        ]
    )
    fake = Fake(lambda request: next(replies))
    client, _ = _client(fake, tmp_path)
    client.get(DAILY_BARS, {})
    assert fake.tokens_issued == 2
    assert [q.headers["authorization"] for q in fake.queries()] == [
        "Bearer TOKEN1",
        "Bearer TOKEN2",
    ]


def test_token_rejected_twice_stops(tmp_path: Path) -> None:
    fake = Fake(lambda r: httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00121"}))
    client, _ = _client(fake, tmp_path)
    with pytest.raises(SourceUnavailableError):
        client.get(DAILY_BARS, {})
    assert fake.tokens_issued == 2


def test_token_too_soon_waits_a_minute(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, json={"error_code": "EGW00133"})
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "T", "expires_in": 86400})
        return httpx.Response(200, json=OK)

    sleeps: list[float] = []
    client = KisClient(
        DEMO,
        httpx.Client(transport=httpx.MockTransport(handler)),
        token_cache=TokenCache(tmp_path / "token.json"),
        sleep=sleeps.append,
        clock=lambda: 0.0,
    )
    client.get(DAILY_BARS, {})
    assert 61.0 in sleeps


def test_approval_key(tmp_path: Path) -> None:
    fake = Fake()
    client, _ = _client(fake, tmp_path)
    assert client.approval_key() == "APPROVAL1"
    body = json.loads(fake.requests[0].content)
    assert set(body) == {"grant_type", "appkey", "secretkey"}


# ------------------------------------------------------------------ 오류 · 다시 시도


def test_rate_limit_and_gateway_errors_are_retried_with_backoff(tmp_path: Path) -> None:
    replies = iter(
        [
            httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 초과"}),
            httpx.Response(500, json={"rt_cd": "1", "msg_cd": "EGW00300"}),
            httpx.Response(200, json=OK),
        ]
    )
    client, sleeps = _client(Fake(lambda r: next(replies)), tmp_path, retry_wait_s=1.0)
    assert json.loads(client.get(DAILY_BARS, {}).body)["rt_cd"] == "0"
    assert [s for s in sleeps if s >= 1.0] == [1.0, 2.0]


def test_gives_up_after_max_attempts(tmp_path: Path) -> None:
    client, _ = _client(Fake(lambda r: httpx.Response(503)), tmp_path, max_attempts=3)
    with pytest.raises(SourceUnavailableError):
        client.get(DAILY_BARS, {})


def test_business_error_stops_at_once_without_leaking_secrets(tmp_path: Path) -> None:
    fake = Fake(
        lambda r: httpx.Response(
            200, json={"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 종목"}
        )
    )
    client, _ = _client(fake, tmp_path)
    with pytest.raises(SourceUnavailableError) as raised:
        client.get(DAILY_BARS, {})
    text = str(raised.value)
    assert "OPSQ0002" in text and "PAPERSECRET-TEST" not in text and "TOKEN1" not in text
    assert len(fake.queries()) == 1


def test_body_is_returned_as_received(tmp_path: Path) -> None:
    raw = b'{"rt_cd":"0","msg_cd":"MCA00000","output2":[{"stck_bsop_date":"20240603"}]}'
    client, _ = _client(Fake(lambda r: httpx.Response(200, content=raw)), tmp_path)
    assert client.get(DAILY_BARS, {}).body == raw


# ------------------------------------------------------------------ 간격 · 이어 받기


@pytest.mark.parametrize(("credentials", "gap"), [(DEMO, 0.55), (REAL, 0.06)])
def test_throttle_keeps_environment_interval(
    tmp_path: Path, credentials: Credentials, gap: float
) -> None:
    ticks = iter([0.0, 0.0, 0.01, 1.0])
    sleeps: list[float] = []
    client = KisClient(
        credentials,
        httpx.Client(transport=httpx.MockTransport(Fake().handler)),
        token_cache=TokenCache(tmp_path / "token.json"),
        sleep=sleeps.append,
        clock=lambda: next(ticks),
    )
    client.get(DAILY_BARS, {})  # 토큰 발급(0.0) → 조회(0.0, 간격만큼 기다림) ...
    assert sleeps and sleeps[0] == pytest.approx(gap)


def test_pages_follow_tr_cont_and_context(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("CTX_AREA_NK", "")
        if page == "":
            body = {**OK, "ctx_area_fk": "F1", "ctx_area_nk": "N1"}
            return httpx.Response(200, json=body, headers={"tr_cont": "F"})
        assert request.headers["tr_cont"] == "N"
        return httpx.Response(200, json={**OK, "ctx_area_nk": "N2"}, headers={"tr_cont": "D"})

    fake = Fake(handler)
    client, _ = _client(fake, tmp_path)
    pages = list(client.pages(CHK_HOLIDAY, {"BASS_DT": "20240601"}))
    assert len(pages) == 2
    assert fake.queries()[1].url.params["CTX_AREA_FK"] == "F1"


def test_pages_stop_on_repeated_context(tmp_path: Path) -> None:
    body = {**OK, "ctx_area_fk": "F", "ctx_area_nk": "N"}
    fake = Fake(lambda r: httpx.Response(200, json=body, headers={"tr_cont": "M"}))
    client, _ = _client(fake, tmp_path)
    with pytest.raises(DataIntegrityError):
        list(client.pages(CHK_HOLIDAY, {}))


# ------------------------------------------------------------------ 키


def test_credentials_default_to_demo_and_hide_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "KIS_ACCOUNT_NO=1234567801\nKIS_PAPER_APP_KEY=pk  # 주석\nKIS_PAPER_APP_SECRET='ps'\n"
        "KIS_APP_KEY=rk\nKIS_APP_SECRET=rs\n",
        encoding="utf-8",
    )
    demo = Credentials.from_env({}, env_file)
    assert (demo.env, demo.app_key, demo.app_secret) == (KisEnv.DEMO, "pk", "ps")
    assert "pk" not in repr(demo) and "ps" not in repr(demo)
    real = Credentials.from_env({"MDP_KIS_ENV": "real"}, env_file)
    assert (real.app_key, real.app_secret) == ("rk", "rs")
    assert not hasattr(real, "account")


def test_missing_keys_name_what_to_add(tmp_path: Path) -> None:
    with pytest.raises(MissingCredentialsError) as raised:
        Credentials.from_env({}, tmp_path / "없음.env")
    assert "KIS_PAPER_APP_KEY" in str(raised.value)
