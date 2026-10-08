from __future__ import annotations

import random
from collections import Counter

from scripts.jev_brief_numbers import claims, synthetic_brief, validator_says

from auto_trading.ops.market_brief import describe_numbers


def test_claims_balance_and_current_validator_blind_spots() -> None:
    rng = random.Random(3)
    brief = synthetic_brief(rng, 0)
    table = describe_numbers(brief)
    items = claims(rng, brief)
    assert Counter(t for _, t, _ in items) == {True: 5, False: 5}
    assert Counter(k for _, _, k in items) == {"true": 5, "sign": 2, "ticker": 2, "number": 1}
    for text, _truth, kind in items:
        if kind in ("true", "sign", "ticker"):
            assert validator_says(text, table)  # 숫자는 표에 있다 — 부호 · 종목 오류는 못 잡는다
    nvda = [r["ret"] for c in brief["contrib"].values() for r in c["rows"] if r["symbol"] == "NVDA"]
    assert len(set(nvda)) == 1  # 두 목록에서 같은 수익률
