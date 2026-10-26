"""한국투자증권 접속 창구 — 허용한 시세 조회만 보내고, 응답 본문을 받은 그대로 돌려준다(K-1).

언제 쓰나: 국내 주식 Bronze 받기(K-2) · 실시간 체결 받기(K-4)가 원천에 물을 때.
읽고 쓰는 것:
- 읽기: 환경 변수(아래 표, 없으면 저장소 ``.env`` 에서 그 이름 줄만), 원천 HTTP 응답
- 쓰기: 접근 토큰 캐시 ``data/kis/token-<환경>.json`` (Git 밖, 앱 키 지문으로 다른 키의 토큰을 버림)

- 모의투자 ``demo`` (기본): ``openapivts.koreainvestment.com:29443``,
  키 ``KIS_PAPER_APP_KEY`` · ``KIS_PAPER_APP_SECRET``
- 실전 ``real``: ``openapi.koreainvestment.com:9443``, 키 ``KIS_APP_KEY`` · ``KIS_APP_SECRET``

환경은 ``MDP_KIS_ENV`` 로 고른다.

계좌 번호는 읽지 않는다 — 시세 조회에는 필요 없다.

허용 목록 (이 밖의 요청은 보내기 전에 :class:`~mdp.errors.ForbiddenRequestError`):

| 무엇 | 방법 · 경로 | TR |
|---|---|---|
| 접근 토큰 · 웹소켓 접속키 | ``POST /oauth2/tokenP`` · ``POST /oauth2/Approval`` | — |
| 일봉 (수정주가 고르기) | ``GET …/quotations/inquire-daily-itemchartprice`` | ``FHKST03010100`` |
| 당일 분봉 | ``GET …/quotations/inquire-time-itemchartprice`` | ``FHKST03010200`` |
| 지난 날 분봉 (1 년 보관) | ``GET …/quotations/inquire-time-dailychartprice`` | ``FHKST03010230`` |
| 휴장일 | ``GET …/quotations/chk-holiday`` | ``CTCA0903R`` |

(``…`` = ``/uapi/domestic-stock/v1``. 경로 · TR 은 공식 예제 저장소
``koreainvestment/open-trading-api`` 에서 2026-10-26 확인.)

[이유] 같은 키로 주문 · 잔고 · 계좌 경로도 부를 수 있다. 키는 시세 조회에만 쓰므로(절대 규칙 2),
       코드 실수로도 닿지 않게 '보낼 수 있는 것' 을 (방법, 경로, TR) 목록으로 못 박는다. 기본 환경을
       모의투자로 두어 실전 키가 없어도 돈 있는 계좌와 떨어져 있게 한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import httpx

from mdp.errors import DataIntegrityError, ForbiddenRequestError, SourceUnavailableError

__all__ = [
    "CHK_HOLIDAY",
    "DAILY_BARS",
    "MINUTE_BARS_PAST",
    "MINUTE_BARS_TODAY",
    "Credentials",
    "KisClient",
    "KisEnv",
    "KisResponse",
    "MissingCredentialsError",
    "Quotation",
    "TokenCache",
]

log = logging.getLogger(__name__)

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
ENV_VAR: Final = "MDP_KIS_ENV"

TOKEN_ERRORS: Final = frozenset({"EGW00121", "EGW00123"})
"""토큰 무효 · 만료 — 한 번 다시 발급받는다 (원본 저장소 실측 코드)."""
RATE_ERRORS: Final = frozenset({"EGW00201"})
"""초당 거래건수 초과."""
GATEWAY_ERRORS: Final = frozenset({"EGW00300"})
"""게이트웨이 오류 — 몇 분 뒤 풀렸다(원본 저장소 2026-09-26 실측)."""
TOKEN_TOO_SOON: Final = "EGW00133"
"""토큰 발급은 1 분에 한 번(공식 예제 저장소)."""
TOKEN_MARGIN_S: Final = 600.0
"""만료 10 분 전에 새로 받는다."""


class KisEnv(StrEnum):
    """[쓰임] 접속 환경 — 모의투자(기본) 또는 실전. 주소 · 키 이름 · 요청 간격이 다르다."""

    DEMO = "demo"
    REAL = "real"

    @property
    def rest_host(self) -> str:
        """[쓰임] REST 호스트."""
        if self is KisEnv.DEMO:
            return "openapivts.koreainvestment.com"
        return "openapi.koreainvestment.com"

    @property
    def rest_port(self) -> int:
        """[쓰임] REST 포트."""
        return 29443 if self is KisEnv.DEMO else 9443

    @property
    def ws_url(self) -> str:
        """[쓰임] 실시간 웹소켓 주소 (K-4).
        공식 설정 파일 그대로 ``ws://`` (암호화 없음 — 확인 필요)."""
        return f"ws://ops.koreainvestment.com:{31000 if self is KisEnv.DEMO else 21000}"

    @property
    def min_interval_s(self) -> float:
        """[쓰임] 요청 사이 최소 간격. 공식 예제의 간격(실전 0.05 초 · 모의 0.5 초)보다 조금 길게.
        [이유] 공식 문서에 '모의투자는 호출 제한이 낮다' 고만 있고 정확한 숫자는 확인 필요."""
        return 0.55 if self is KisEnv.DEMO else 0.06

    @property
    def key_vars(self) -> tuple[str, str]:
        """[쓰임] 앱 키 · 앱 시크릿 환경 변수 이름."""
        if self is KisEnv.DEMO:
            return ("KIS_PAPER_APP_KEY", "KIS_PAPER_APP_SECRET")
        return ("KIS_APP_KEY", "KIS_APP_SECRET")

    def url(self, path: str) -> str:
        """[쓰임] 요청 주소."""
        return f"https://{self.rest_host}:{self.rest_port}{path}"


@dataclass(frozen=True, slots=True)
class Quotation:
    """[쓰임] 허용한 시세 조회 하나 — 경로와 TR (방법은 언제나 GET)."""

    path: str
    tr_id: str
    name: str


_DOMESTIC: Final = "/uapi/domestic-stock/v1/quotations"
DAILY_BARS: Final = Quotation(f"{_DOMESTIC}/inquire-daily-itemchartprice", "FHKST03010100", "일봉")
MINUTE_BARS_TODAY: Final = Quotation(
    f"{_DOMESTIC}/inquire-time-itemchartprice", "FHKST03010200", "당일 분봉"
)
MINUTE_BARS_PAST: Final = Quotation(
    f"{_DOMESTIC}/inquire-time-dailychartprice", "FHKST03010230", "지난 날 분봉"
)
CHK_HOLIDAY: Final = Quotation(f"{_DOMESTIC}/chk-holiday", "CTCA0903R", "휴장일")
ALLOWED_GET: Final = frozenset({DAILY_BARS, MINUTE_BARS_TODAY, MINUTE_BARS_PAST, CHK_HOLIDAY})
TOKEN_PATH: Final = "/oauth2/tokenP"
APPROVAL_PATH: Final = "/oauth2/Approval"
ALLOWED_POST: Final = frozenset({TOKEN_PATH, APPROVAL_PATH})


class MissingCredentialsError(Exception):
    """[쓰임] 한국투자증권 키가 없다 — 사람이 ``.env`` 에 넣어야 한다."""


@dataclass(frozen=True, slots=True)
class Credentials:
    """[쓰임] 앱 키 한 쌍과 환경. ``repr`` 에 값을 보이지 않는다(로그 · 오류로 새지 않게)."""

    env: KisEnv
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)

    @property
    def fingerprint(self) -> str:
        """[쓰임] 앱 키 지문(SHA-256 앞 16 자) — 토큰 캐시가 다른 키의 토큰을 쓰지 않게."""
        return hashlib.sha256(self.app_key.encode()).hexdigest()[:16]

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None, env_file: Path | None = None
    ) -> Credentials:
        """[쓰임] ``MDP_KIS_ENV``(기본 demo)와 그 환경의 키 두 개를 환경 변수에서,
        없으면 ``.env`` 에서 읽는다.
        [이유] ``.env`` 의 다른 줄(계좌 번호 등)은 읽지 않는다."""
        source = dict(os.environ if environ is None else environ)
        path = env_file if env_file is not None else REPO_ROOT / ".env"
        wanted = {ENV_VAR, *KisEnv.DEMO.key_vars, *KisEnv.REAL.key_vars}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                name, sep, value = line.partition("=")
                name = name.strip()
                if sep and name in wanted and not source.get(name):
                    source[name] = value.split("#", 1)[0].strip().strip("'\"")
        env = KisEnv(source.get(ENV_VAR) or KisEnv.DEMO.value)
        key_var, secret_var = env.key_vars
        app_key, app_secret = source.get(key_var, ""), source.get(secret_var, "")
        if not app_key or not app_secret:
            raise MissingCredentialsError(
                f"한국투자증권 {env.value} 키가 없다 — {key_var} · {secret_var} 를 .env 에 넣어라"
            )
        return cls(env, app_key, app_secret)


@dataclass(frozen=True, slots=True)
class TokenCache:
    """[쓰임] 접근 토큰 파일 캐시 — 앱 키 지문이 같고 만료 10 분 전까지만 쓴다.
    [이유] 토큰은 1 분에 한 번만 발급되고 하루 동안 쓸 수 있다. 명령마다 새로 받으면 연달아 돌릴 때
           '너무 잦은 발급' 으로 막힌다."""

    path: Path

    def load(self, credentials: Credentials, now: float) -> str | None:
        """[쓰임] 쓸 수 있는 토큰. 없거나 다른 키 · 만료 임박이면 None."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("key_fp") != credentials.fingerprint:
            return None
        if float(data.get("expires_at", 0)) - TOKEN_MARGIN_S <= now:
            return None
        token = data.get("token")
        return token if isinstance(token, str) and token else None

    def save(self, credentials: Credentials, token: str, expires_at: float) -> None:
        """[쓰임] 임시 파일에 쓰고 바꿔 끼운다."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        body = {"key_fp": credentials.fingerprint, "token": token, "expires_at": expires_at}
        temporary.write_text(json.dumps(body), encoding="utf-8")
        temporary.replace(self.path)

    def clear(self) -> None:
        """[쓰임] 지운다(토큰 오류를 받았을 때)."""
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class KisResponse:
    """[쓰임] 응답 한 번 — 본문(받은 그대로)과 이어 받기 표시(``tr_cont`` 머리글)."""

    body: bytes
    tr_cont: str

    @property
    def has_next(self) -> bool:
        """[쓰임] 다음 페이지가 있나 (``F`` · ``M`` — 공식 예제)."""
        return self.tr_cont in ("F", "M")


class _RetryableError(Exception):
    """[쓰임] 다시 시도하면 풀릴 수 있는 오류(초당 한도 · 게이트웨이 · 5xx · 네트워크)."""


class KisClient:
    """[쓰임] 허용 목록 검사 · 토큰 · 요청 간격 · 다시 시도 · 이어 받기를 맡는 창구.
    [이유] ``httpx.Client`` · ``sleep`` · 시계를 밖에서 넣을 수 있어, 시험은 가짜 네트워크와
           기다리지 않는 sleep 으로 모든 경로를 돈다."""

    def __init__(
        self,
        credentials: Credentials,
        client: httpx.Client | None = None,
        *,
        token_cache: TokenCache | None = None,
        max_attempts: int = 5,
        retry_wait_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """[쓰임] ``client`` 를 주지 않으면 직접 만들고 :meth:`close` 에서 그것만 닫는다."""
        self.credentials = credentials
        self.env = credentials.env
        cache_path = REPO_ROOT / "data" / "kis" / f"token-{self.env.value}.json"
        self.token_cache = token_cache if token_cache is not None else TokenCache(cache_path)
        self.max_attempts = max_attempts
        self.retry_wait_s = retry_wait_s
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_sent: float | None = None
        self._token: str | None = None
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(timeout=30.0)

    def close(self) -> None:
        """[쓰임] 이 창구가 직접 만든 HTTP 연결만 닫는다."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> KisClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------- 허용 목록

    def check_allowed(self, method: str, url: str, tr_id: str | None) -> None:
        """[쓰임] 보내기 전에 (방법, 호스트, 경로, TR)이 허용 목록에 있는지 본다. 없으면 멈춘다.
        [이유] 주문(``…/trading/order-cash``) · 잔고 · 계좌 경로는 목록에 없다."""
        parsed = httpx.URL(url)
        same_host = (parsed.scheme, parsed.host, parsed.port) == (
            "https",
            self.env.rest_host,
            self.env.rest_port,
        )
        if method == "GET":
            allowed = any(q.path == parsed.path and q.tr_id == tr_id for q in ALLOWED_GET)
        else:
            allowed = method == "POST" and parsed.path in ALLOWED_POST and tr_id is None
        if not (same_host and allowed):
            raise ForbiddenRequestError(
                f"허용 목록에 없는 요청이라 보내지 않았다: {method} {url} tr_id={tr_id}"
            )

    # ------------------------------------------------------------- 내부

    def _throttle(self) -> None:
        """[쓰임] 앞 요청과 환경별 최소 간격 이상 떨어지게 기다린다."""
        now = self._clock()
        if self._last_sent is not None:
            wait = self.env.min_interval_s - (now - self._last_sent)
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
        self._last_sent = now

    def _post_auth(self, path: str, body: Mapping[str, str]) -> dict[str, Any]:
        """[쓰임] 토큰 · 접속키 발급.
        1 분 안에 다시 받으려 하면(EGW00133) 61 초 기다렸다가 한 번 더."""
        url = self.env.url(path)
        self.check_allowed("POST", url, None)
        for attempt in (1, 2):
            self._throttle()
            response = self._client.post(url, json=dict(body))
            data = _json_or_empty(response)
            if response.is_success and data:
                return data
            if str(data.get("error_code") or data.get("msg_cd")) == TOKEN_TOO_SOON and attempt == 1:
                self._sleep(61.0)
                continue
            break
        raise SourceUnavailableError(
            f"HTTP {response.status_code}: 발급 실패 {path}"
            f" — 키와 환경({self.env.value})이 맞는지 본다"
        )

    def _access_token(self) -> str:
        """[쓰임] 캐시 → 없으면 새로 발급. 발급 응답의 ``expires_in``(초)로 만료를 적는다."""
        if self._token is None:
            self._token = self.token_cache.load(self.credentials, self._wall_clock())
        if self._token is None:
            data = self._post_auth(
                TOKEN_PATH,
                {
                    "grant_type": "client_credentials",
                    "appkey": self.credentials.app_key,
                    "appsecret": self.credentials.app_secret,
                },
            )
            token = str(data.get("access_token") or "")
            if not token:
                raise SourceUnavailableError("토큰 발급 응답에 access_token 이 없다")
            expires_in = float(data.get("expires_in") or 86_400)
            self.token_cache.save(self.credentials, token, self._wall_clock() + expires_in)
            self._token = token
        return self._token

    def _send_once(
        self, quotation: Quotation, params: Mapping[str, str], tr_cont: str
    ) -> KisResponse:
        """[쓰임] 한 번 보낸다. 성공(``rt_cd == "0"``)이 아니면 오류를 갈라 던진다."""
        self._throttle()
        response = self._client.get(
            self.env.url(quotation.path),
            params=dict(params),
            headers={
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._access_token()}",
                "appkey": self.credentials.app_key,
                "appsecret": self.credentials.app_secret,
                "tr_id": quotation.tr_id,
                "custtype": "P",
                "tr_cont": tr_cont,
            },
        )
        data = _json_or_empty(response)
        code = str(data.get("msg_cd") or "")
        if response.is_success and str(data.get("rt_cd")) == "0":
            return KisResponse(response.content, response.headers.get("tr_cont", ""))
        if code in TOKEN_ERRORS:
            raise _TokenRejectedError(code)
        if code in RATE_ERRORS or code in GATEWAY_ERRORS or response.status_code >= 500:
            raise _RetryableError(f"HTTP {response.status_code} {code}: {quotation.name}")
        message = str(data.get("msg1") or "").strip()
        raise SourceUnavailableError(
            f"HTTP {response.status_code} {code}: {message} ({quotation.name} {quotation.tr_id})"
        )

    # ------------------------------------------------------------- 공개

    def approval_key(self) -> str:
        """[쓰임] 실시간 웹소켓 접속키 (K-4)."""
        data = self._post_auth(
            APPROVAL_PATH,
            {
                "grant_type": "client_credentials",
                "appkey": self.credentials.app_key,
                "secretkey": self.credentials.app_secret,
            },
        )
        key = str(data.get("approval_key") or "")
        if not key:
            raise SourceUnavailableError("접속키 발급 응답에 approval_key 가 없다")
        return key

    def get(
        self, quotation: Quotation, params: Mapping[str, str], tr_cont: str = ""
    ) -> KisResponse:
        """[쓰임] 한 번 묻고 응답 본문을 받은 그대로 돌려준다.
        토큰 오류는 한 번 새로 발급, 다시 시도할 오류는 시도마다 두 배로 기다린다."""
        self.check_allowed("GET", self.env.url(quotation.path), quotation.tr_id)
        token_refreshed = False
        last_error: Exception | None = None
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            try:
                return self._send_once(quotation, params, tr_cont)
            except _TokenRejectedError:
                if token_refreshed:
                    raise SourceUnavailableError("새 토큰도 거절됐다") from None
                token_refreshed = True
                attempt -= 1
                self._token = None
                self.token_cache.clear()
            except (_RetryableError, httpx.TransportError) as exc:
                last_error = exc
                log.warning(
                    json.dumps(
                        {
                            "event": "retry",
                            "tr_id": quotation.tr_id,
                            "attempt": attempt,
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )
                if attempt < self.max_attempts:
                    self._sleep(self.retry_wait_s * 2 ** (attempt - 1))
        raise SourceUnavailableError(
            f"{self.max_attempts}번 시도해도 받지 못했다: {quotation.name}"
        ) from last_error

    def pages(self, quotation: Quotation, params: Mapping[str, str]) -> Iterator[bytes]:
        """[쓰임] ``tr_cont`` 가 '다음 있음' 인 동안 이어 받으며 페이지 본문을 하나씩 내준다.
        [이유] 공식 예제의 방식 — 다음 요청은 ``tr_cont=N`` 과
               응답의 ``ctx_area_fk`` · ``ctx_area_nk`` 를 넣는다.
               같은 위치가 다시 오면 끝없이 돌 수 있어 멈춘다. 봉 조회처럼 시각으로 이어 받는
               것은 받기 쪽(K-2)이 인자를 바꿔 :meth:`get` 을 부른다."""
        query = dict(params)
        seen: set[tuple[str, str]] = set()
        response = self.get(quotation, query)
        while True:
            yield response.body
            if not response.has_next:
                return
            decoded = json.loads(response.body)
            position = (str(decoded.get("ctx_area_fk", "")), str(decoded.get("ctx_area_nk", "")))
            if position in seen:
                raise DataIntegrityError(f"같은 이어 받기 위치가 다시 왔다: {quotation.name}")
            seen.add(position)
            query["CTX_AREA_FK"], query["CTX_AREA_NK"] = position
            response = self.get(quotation, query, tr_cont="N")


class _TokenRejectedError(Exception):
    """[쓰임] 토큰 무효 · 만료 응답 — 한 번 새로 발급받는다."""


def _json_or_empty(response: httpx.Response) -> dict[str, Any]:
    """[쓰임] 응답 JSON(객체). 비었거나 JSON 이 아니면 빈 사전."""
    try:
        decoded = response.json() if response.content else {}
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
