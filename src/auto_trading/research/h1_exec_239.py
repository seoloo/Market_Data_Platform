"""문서 239 — H1 숏 지정가 체결 모형 (순수 계산).

docs/research/239-h1-short-limit-execution.md. 1 분봉 받기 · 실행은 ``scripts/run_h1_exec_239.py``.
1 분봉 한 줄 = (열린 ms, 시가, 고가, 저가, 종가).
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = [
    "MAKER",
    "TAKER",
    "WAIT_MIN",
    "entry_filled",
    "exit_fill",
    "short_return",
]

MAKER = 0.0002
TAKER = 0.0005
WAIT_MIN = 60
THROUGH = 0.0001
"""가격을 이만큼 뚫고 지나가야 체결로 본다 (줄 맨 끝 대기열 가정 대신 보수적으로)."""
SIZE = 0.5

Bar1m = tuple[int, float, float, float, float]


def entry_filled(bars: Sequence[Bar1m], price: float, wait: int = WAIT_MIN) -> bool:
    """지정가 매도 ``price`` 가 ``wait`` 분 안에 체결되는가 (고가 ≥ price × (1 + 0.01%))."""
    return any(b[2] >= price * (1 + THROUGH) for b in bars[:wait])


def exit_fill(
    bars: Sequence[Bar1m], price: float, wait: int = WAIT_MIN
) -> tuple[float, float] | None:
    """지정가 매수 ``price`` 청산 → (체결가, 수수료). 안 되면 ``wait`` 분째 종가에 테이커.

    1 분봉이 ``wait`` 개보다 적고 체결도 안 됐으면 None (자료 부족)."""
    for b in bars[:wait]:
        if b[3] <= price * (1 - THROUGH):
            return price, MAKER
    if len(bars) < wait:
        return None
    return bars[wait - 1][4], TAKER


def short_return(
    entry: float, exit_px: float, funding: float, fee_in: float, fee_out: float
) -> float:
    """자본 대비 순수익 (숏 크기 0.5 배)."""
    return SIZE * ((entry - exit_px) / entry + funding - fee_in - fee_out * exit_px / entry)
