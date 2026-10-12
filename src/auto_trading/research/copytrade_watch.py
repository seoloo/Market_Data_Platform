"""문서 226 후속 — 고른 카피 트레이딩 리더 10 명을 앞으로 90 일 기록 (순수 계산).

받기 · 저장은 ``scripts/copytrade_watch.py``. 주문 없음.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = ["forward_summary", "merge_new", "position_key"]


def position_key(p: Mapping[str, Any]) -> str:
    """같은 포지션을 두 번 세지 않는 열쇠 (포지션 번호 + 닫힌 시각)."""
    return f"{p.get('positionId') or p.get('id')}|{p.get('closed')}"


def merge_new(
    seen: set[str], rows: Iterable[Mapping[str, Any]], start_ms: int
) -> list[dict[str, Any]]:
    """처음 보는 닫힌 포지션 중 ``start_ms`` 이후에 닫힌 것만 (``seen`` 을 갱신)."""
    out = []
    for r in rows:
        if not r.get("closed") or int(r["closed"]) < start_ms:
            continue
        k = position_key(r)
        if k in seen:
            continue
        seen.add(k)
        out.append(dict(r))
    out.sort(key=lambda r: int(r["closed"]))
    return out


def forward_summary(
    rows: Sequence[Mapping[str, Any]], avg_win_before: float | None
) -> dict[str, Any]:
    """기록 시작 뒤 닫힌 포지션의 합계 · 닫힌 손익 기준 최대 낙폭 · 최악 손실."""
    pnl = [float(r["closingPnl"]) for r in sorted(rows, key=lambda r: int(r["closed"]))]
    peak = cum = mdd = 0.0
    for x in pnl:
        cum += x
        peak = max(peak, cum)
        mdd = max(mdd, peak - cum)
    worst = min(pnl) if pnl else 0.0
    return {
        "positions": len(pnl),
        "pnl": cum,
        "win_rate": (sum(x > 0 for x in pnl) / len(pnl)) if pnl else None,
        "max_drawdown_usdt": mdd,
        "worst": worst,
        "worst_over_avg_win_before": (worst / avg_win_before) if avg_win_before else None,
    }
