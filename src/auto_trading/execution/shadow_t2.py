"""T/v2 실거래 그림자 운영 — 주문 없이 실거래 크기로 돌리는 점검 (Phase 8 준비, docs/research/51).

그림자 운영은 **주문을 보내지 않는다.** 하는 일:

1. Paper 와 같은 실행기(:class:`~auto_trading.execution.paper_t2.PaperT2Runner`)를
   실거래 예정 금액으로 돌린다 (``mode='shadow'``, 별도 원장).
   작은 금액에서 수량 단위·최소 주문 금액에 걸리는지가 드러난다.
2. 실제 계좌를 **읽기만** 해서 실거래 준비 상태를 점검한다 (:func:`readiness_findings`):
   키가 여전히 읽기 전용·IP 제한인지, 서버 시계 차이, 실거래 예정 금액만큼 잔고가 있는지.
3. 그날 낼 주문 크기로 **호가를 읽어 체결 미끄러짐을 추정**한다 (:func:`book_slippage_bps`).
   연구 비용 가정(현물 슬리피지 0.05%)이 실거래 크기에서 맞는지 본다.

이 모듈의 함수는 순수하다(I/O 없음). 거래소 호출과 기록은 ``scripts/run_shadow_t2.py`` 가 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["MUST_BE_OFF", "ShadowFinding", "book_slippage_bps", "readiness_findings"]

MUST_BE_OFF = (
    "enableSpotAndMarginTrading",
    "enableWithdrawals",
    "enableFutures",
    "enableMargin",
    "enableInternalTransfer",
    "permitsUniversalTransfer",
    "enableVanillaOptions",
    "enablePortfolioMarginTrading",
)
"""Phase 8 승인 전 실계좌 키에서 꺼져 있어야 하는 권한 (scripts/check_spot_account.py 와 같다)."""

_MAX_CLOCK_OFFSET_MS = 1000
_QUOTE = "USDT"


@dataclass(frozen=True, slots=True)
class ShadowFinding:
    level: str
    """INFO · WARN · CRITICAL."""
    message: str


def book_slippage_bps(
    levels: Sequence[tuple[Decimal, Decimal]], notional: Decimal, *, mid: Decimal
) -> Decimal | None:
    """시장가로 ``notional`` 만큼 먹을 때 평균 체결가가 중간가에서 불리하게 벗어나는 정도(bps).

    ``levels`` 는 먹는 쪽 호가(매수면 매도 호가)를 좋은 가격부터. 호가가 모자라면 None.
    """
    if notional <= 0 or mid <= 0:
        raise ValueError("주문 금액과 중간가는 양수여야 한다")
    remaining = notional
    qty = Decimal(0)
    spent = Decimal(0)
    for price, size in levels:
        take = min(remaining, price * size)
        qty += take / price
        spent += take
        remaining -= take
        if remaining <= 0:
            break
    if remaining > 0 or qty == 0:
        return None
    avg = spent / qty
    return abs(avg - mid) / mid * Decimal(10_000)


def readiness_findings(
    *,
    restrictions: Mapping[str, bool] | None,
    clock_offset_ms: int | None,
    balances: Mapping[str, Decimal] | None,
    prices: Mapping[str, Decimal],
    allocation: Decimal,
    halted_reason: str | None,
    rejected_orders: Iterable[str] = (),
    account_error: str | None = None,
) -> list[ShadowFinding]:
    """실거래였다면 문제가 됐을 것들. 그림자 운영 자체는 멈추지 않는다(보고만).

    ``restrictions``·``balances`` 가 None 이면 키가 없거나 조회에 실패한 것이다.
    """
    out: list[ShadowFinding] = []
    if halted_reason is not None:
        out.append(
            ShadowFinding(
                "WARN", f"긴급 정지 스위치 켜짐 — 실거래였다면 주문 안 함: {halted_reason}"
            )
        )
    if account_error is not None and "-2015" in account_error:
        # Binance -2015: 키·IP·권한 중 하나가 맞지 않음.
        # 집 PC 는 공인 IP 가 바뀔 수 있다 (문서 84 §3)
        out.append(
            ShadowFinding(
                "WARN",
                "실계좌 키가 거부됨(-2015) — 집 공인 IP 가 바뀌었을 수 있다."
                " Binance API 관리에서 키의 IP 목록을 확인",
            )
        )
    if restrictions is None:
        out.append(ShadowFinding("WARN", "실계좌 키 권한을 읽지 못함 (키 없음 또는 조회 실패)"))
    else:
        risky = sorted(k for k in MUST_BE_OFF if restrictions.get(k))
        if risky:
            out.append(
                ShadowFinding(
                    "CRITICAL", f"Phase 8 승인 전인데 키에 읽기 외 권한이 켜져 있음: {risky}"
                )
            )
        if not restrictions.get("ipRestrict"):
            out.append(ShadowFinding("CRITICAL", "키에 IP 제한이 없음"))
        if not restrictions.get("enableReading"):
            out.append(ShadowFinding("WARN", "키의 읽기 권한이 꺼져 있음"))
    if clock_offset_ms is None:
        out.append(ShadowFinding("WARN", "서버 시계 차이를 재지 못함"))
    elif abs(clock_offset_ms) > _MAX_CLOCK_OFFSET_MS:
        out.append(
            ShadowFinding("WARN", f"서버 시계 차이 {clock_offset_ms} ms > {_MAX_CLOCK_OFFSET_MS}")
        )
    if balances is None:
        out.append(ShadowFinding("WARN", "실계좌 잔고를 읽지 못함"))
    else:
        value = balances.get(_QUOTE, Decimal(0))
        for symbol, price in prices.items():
            value += balances.get(symbol.removesuffix(_QUOTE), Decimal(0)) * price
        if value < allocation:
            out.append(
                ShadowFinding(
                    "INFO",
                    f"실계좌 평가액 {value:.2f} USDT < 실거래 예정 금액 {allocation} USDT "
                    "(실거래 시작 전에 입금 필요)",
                )
            )
    for note in rejected_orders:
        out.append(ShadowFinding("WARN", f"주문 규칙에 걸려 건너뛴 주문: {note}"))
    return out
