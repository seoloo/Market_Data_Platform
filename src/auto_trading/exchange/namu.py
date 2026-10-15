"""NH투자증권 나무 플러그 Open API — 국내주식 **모의투자 전용** 클라이언트.

사용자 (2026-10-15): "모의 투자 한번 진행해줘" — 국장 주도주를 나무증권 실전으로 옮기기 전에
주문 경로(잔고 · 현재가 · 지정가 매수 · 체결 조회 · 취소 · 매도)를 모의투자 계좌로 검증한다.

* 정본: https://www.nhplug.com/llms.txt · ``openapi-docs/krstock/openapi.json`` (명세서 260911).
* 주문 · 계좌 조회는 모의투자 도메인(``moapi``)과 ``acct_type == "03"`` 계좌만 쓴다
  (시세만 운영 도메인에서 조회 — 모의투자는 시세 API 를 주지 않는다). 운영 주소로는
  만들 수 없다(:class:`NamuMock` 생성 시 확인). 실전 주문은 이 파일 범위가 아니다.
* 토큰 발급은 운영 도메인에서만 되고 **발급할 때마다 보안 알림**이 가므로 파일
  (``data/namu_token.json``)에 만료 시각과 함께 두고 재사용한다. 401 일 때만 새로 받는다.
* 성공 판정은 응답 코드 하나로 하지 않는다(문서 '공통 규약') — 기대한 블록이 있는지 보고,
  없으면 메시지를 담아 :class:`NamuError` 를 낸다.
* 지정가만 쓴다(사용자 규칙 2026-10-05 '시장가 금지').
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import NamuCredentials
from auto_trading.core.errors import ExchangeError

__all__ = ["LIVE_URL", "MOCK_URL", "NamuError", "NamuMock", "NamuOrder", "NamuTokenCache"]

LIVE_URL = "https://api.nhplug.com:8443"
"""운영 — 토큰 발급만 여기서 한다."""
MOCK_URL = "https://moapi.nhplug.com:8443"
MOCK_ACCOUNT = "03"
LIMIT = "01"
"""호가유형 01 = 보통가(지정가)."""
GAP_S = 0.25
"""초당 5 회 제한 — 호출 사이 간격."""


class NamuError(ExchangeError):
    def __init__(self, path: str, code: str, message: str) -> None:
        super().__init__(f"나무 {path}: [{code}] {message}")
        self.code = code
        self.message = message


@dataclass(slots=True)
class NamuTokenCache:
    path: Path

    def load(self, now_s: float) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        token, exp = data.get("token"), float(data.get("exp") or 0)
        return str(token) if token and exp - 600 > now_s else None

    def save(self, token: str, exp_s: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"token": token, "exp": exp_s}), encoding="utf-8")


@dataclass(frozen=True, slots=True)
class NamuOrder:
    order_no: int
    code: str
    name: str
    side: str
    qty: int
    price: float
    filled: int
    avg_price: float
    open_qty: int
    canceled: int
    reject: str


@dataclass(slots=True)
class NamuMock:
    """모의투자 국내주식 — 잔고 · 현재가 · 지정가 매수/매도 · 체결 조회 · 취소."""

    credentials: NamuCredentials
    cache: NamuTokenCache
    base_url: str = MOCK_URL
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20.0))
    clock: Callable[[], float] = time.time
    sleep: Callable[[float], None] = time.sleep
    _account: str | None = None
    _last: float = 0.0

    def __post_init__(self) -> None:
        if self.base_url != MOCK_URL:
            raise ValueError("NamuMock 은 모의투자 주소에서만 쓴다")

    def __repr__(self) -> str:
        return f"NamuMock({self.base_url})"

    # ------------------------------------------------------------ 인증

    def _token(self, *, renew: bool = False) -> str:
        now = self.clock()
        cached = None if renew else self.cache.load(now)
        if cached:
            return cached
        r = self.client.post(
            f"{LIVE_URL}/oauth2/token",
            params={
                "appkey": self.credentials.app_key,
                "appsecretkey": self.credentials.app_secret,
                "grant_type": "client_credentials",
                "scope": "oob",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise NamuError("/oauth2/token", str(r.status_code), r.text[:200])
        data = r.json()
        token = str(data["access_token"])
        self.cache.save(token, now + float(data.get("expires_in") or 86400))
        return token

    def _post(self, path: str, body: dict[str, Any], *, base: str | None = None) -> dict[str, Any]:
        wait = GAP_S - (self.clock() - self._last)
        if wait > 0:
            self.sleep(wait)
        url = f"{base or self.base_url}{path}"
        renew, backoff = False, 1.0
        while True:
            headers = {"Authorization": f"Bearer {self._token(renew=renew)}"}
            r = self.client.post(url, json={"Input_0": body}, headers=headers)
            self._last = self.clock()
            if r.status_code == 401 and not renew:
                renew = True  # 토큰 무효일 때만 다시 받는다
                continue
            if r.status_code == 429 and backoff <= 4.0:
                self.sleep(backoff)  # 유량 초과 — 토큰은 그대로, 간격만 늘린다
                backoff *= 2
                continue
            break
        try:
            data = r.json()
        except ValueError as exc:
            raise NamuError(path, str(r.status_code), r.text[:200]) from exc
        if r.status_code != 200:
            raise NamuError(path, str(r.status_code), _msg(data))
        return dict(data)

    # ------------------------------------------------------------ 계좌 · 조회

    def account(self) -> str:
        """모의투자(03) 계좌번호. 없으면 오류 — 첫 계좌를 그대로 쓰지 않는다."""
        if self._account is None:
            data = self._post("/n2/acctinfo", {}, base=LIVE_URL)
            mocks = [
                str(a["acct_no"])
                for a in data.get("Output_0") or []
                if str(a.get("acct_type")) == MOCK_ACCOUNT
            ]
            if not mocks:
                raise NamuError("/n2/acctinfo", "-", "모의투자(03) 계좌가 없다")
            self._account = mocks[0]
        return self._account

    def balance(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """(예수금 · 총평가 등, 보유 종목 목록)."""
        path = "/krstock/inquiry/v1/balance"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "bnc_bse_cd": "5",
                "ltg_aot_dit_cd": "1",
                "aet_bse": "1",
                "qut_dit_cd": "KRX",
                "aly_qut_cd": "1",
            },
        )
        head = data.get("Output_0")
        if not isinstance(head, dict):
            raise NamuError(path, _code(data), _msg(data))
        return head, list(data.get("Output_1") or [])

    def quote(self, code: str) -> dict[str, Any]:
        """현재가 · 호가 — 모의투자 서버는 시세를 주지 않아("모의투자에서는 제공하지 않는 API")
        운영 서버에서 조회만 한다."""
        path = "/krstock/quote/v1/currentPrice"
        data = self._post(path, {"market_cd": "KRX", "iem_cd": code}, base=LIVE_URL)
        out = data.get("Output_0")
        if not isinstance(out, dict) or not out.get("stck_prpr"):
            raise NamuError(path, _code(data), _msg(data))
        return out

    def orders(self, day: str) -> list[NamuOrder]:
        """그날(YYYYMMDD) 주문 · 체결 (전체)."""
        data = self._post(
            "/krstock/inquiry/v1/dailyOrderExecution",
            {
                "orr_dt": day,
                "act_no": self.account(),
                "orr_mkt_cd": "00",
                "ost_cns_dit": "0",
            },
        )
        return [
            NamuOrder(
                order_no=int(o.get("itg_orr_no") or 0),
                code=str(o.get("iem_cd") or "").strip(),
                name=str(o.get("iem_nm") or "").strip(),
                side=str(o.get("sby_dit_cd_nm") or "").strip(),
                qty=int(o.get("orr_qty") or 0),
                price=float(o.get("orr_pr") or 0),
                filled=int(o.get("tot_cns_qty") or 0),
                avg_price=float(o.get("cns_avg_uit_pr") or 0),
                open_qty=int(o.get("ny_cns_qty") or 0),
                canceled=int(o.get("can_qty") or 0),
                reject=str(o.get("orr_rjt_rsn_cd_nm") or "").strip(),
            )
            for o in data.get("Output_0") or []
        ]

    # ------------------------------------------------------------ 주문 (지정가만)

    def limit(self, side: str, code: str, qty: int, price: int) -> int:
        """지정가 주문 → 시장주문번호. ``side`` 는 'buy' · 'sell'."""
        if side not in ("buy", "sell") or qty <= 0 or price <= 0:
            raise ValueError(f"잘못된 주문: {side} {code} {qty}@{price}")
        path = f"/krstock/order/v1/{'cashBuy' if side == 'buy' else 'cashSell'}"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "iem_cd": code,
                "orr_qty": qty,
                "orr_pr": price,
                "nmn_pr_tp_cd": LIMIT,
                "orr_cnd_dit_cd": "00",
                "ssl_nmn_pr_dit_cd": "00",
                "rmt_mkt_cd": "KRX",
                "sor_mkt_sli_yn": "N",
            },
        )
        out = data.get("Output_0")
        if not isinstance(out, dict) or not out.get("mkt_orr_no"):
            raise NamuError(path, _code(data), _msg(data))
        return int(out["mkt_orr_no"])

    def cancel(self, order_no: int, code: str) -> int:
        path = "/krstock/order/v1/cancel"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "org_mkt_orr_no": order_no,
                "all_pat_dit_cd": "1",
                "iem_cd": code,
            },
        )
        out = data.get("Output_0")
        if not isinstance(out, dict) or not out.get("mkt_orr_no"):
            raise NamuError(path, _code(data), _msg(data))
        return int(out["mkt_orr_no"])

    def close(self) -> None:
        self.client.close()


def _msg(data: Any) -> str:
    if not isinstance(data, dict):
        return str(data)[:200]
    m = data.get("message") or {}
    return str(m.get("usr_msg") or data.get("rsp_msg") or m.get("dvlp_msg") or "")[:200]


def _code(data: Any) -> str:
    if not isinstance(data, dict):
        return "-"
    m = data.get("message") or {}
    return str(m.get("msg_code") or data.get("rsp_cd") or "-")
