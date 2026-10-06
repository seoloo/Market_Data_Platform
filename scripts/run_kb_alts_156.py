"""문서 156 — IDEAS 3: KB(139, EMA 정배열 + 박스 돌파) 4시간봉 그대로 알트코인 8 개에. 1회 판정.

사용 예::

    python -m scripts.run_kb_alts_156 audit   # 자료 해시 · 봉 수 · 신호 수(수익 없음)
    python -m scripts.run_kb_alts_156 run     # 깨끗한 트리(IDEAS.md 예외) · 해시 일치

139 의 엔진(``backtest.ema_box`` 의 ``simulate_box`` · ``hold_alignment``)과 설정(``BoxConfig()``),
구간(2018-01 ~ 2026-08)·반쪽·몫 크기·비용(편도 0.15%)·판정(K1 ~ K4)을 그대로 쓴다. 바뀌는 것은
종목: BNB · LTC · ADA · XRP · TRX · LINK · DOGE · SOL (바이낸스 현물 공식 4시간봉, 상장 달부터).
지금 살아 있는 코인만이라 생존 편향이 있다 — 통과해도 상한으로만 읽는다.
"""

from __future__ import annotations

import hashlib
import io
import json
import statistics
import sys
import zipfile
from array import array
from pathlib import Path
from typing import Any

from scripts.run_ema_box import BASE, FROM, SLEEVE, TO, view
from scripts.run_kq_154 import dirty_paths, foreign_changes

from auto_trading.backtest.ema_box import Bars, hold_alignment, simulate_box

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision" / "spot"
OUTPUT = REPO / "reports" / "kb_alts_156"
SYMBOLS = ("BNBUSDT", "LTCUSDT", "ADAUSDT", "XRPUSDT", "TRXUSDT", "LINKUSDT", "DOGEUSDT",
           "SOLUSDT")  # fmt: skip
EXPECTED_DATA: str | None = "064b8969df595de7c7f6d671662f63bb5a5d35f9198279e0fa3f6ff6a1e1ad37"
"""자료 묶음 해시 — audit 뒤 문서 156 에 적고 여기에 고정한다."""


def load_4h(symbol: str) -> Bars:
    t, o, h, lo, c, v = (array("q"), array("d"), array("d"), array("d"), array("d"), array("d"))
    for p in sorted((VISION / symbol / "4h").glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            text = zf.read(zf.namelist()[0]).decode("ascii")
        for line in io.StringIO(text):
            a = line.strip().split(",")
            if not a[0].isdigit():
                continue
            ms = int(a[0])
            ms = ms // 1000 if ms > 10**14 else ms
            if t and ms <= t[-1]:
                continue
            t.append(ms)
            o.append(float(a[1]))
            h.append(float(a[2]))
            lo.append(float(a[3]))
            c.append(float(a[4]))
            v.append(float(a[5]))
    return Bars(t, o, h, lo, c, v)


def data_files() -> list[Path]:
    return sorted(p for s in SYMBOLS for p in (VISION / s / "4h").glob("*.zip"))


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def audit() -> dict[str, Any]:
    digest, n = data_hash()
    out: dict[str, Any] = {"data_sha256": digest, "files": n, "symbols": {}}
    kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": SLEEVE, "cfg": BASE}
    for s in SYMBOLS:
        b = load_4h(s)
        r = simulate_box(b, **kw)
        out["symbols"][s] = {"bars": len(b.t), "signals": r.signals}  # 진입 성과는 안 봄
    return out


def run() -> dict[str, Any]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    others = foreign_changes()
    if others:
        raise SystemExit(f"작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행: {others}")
    from scripts.run_a1_train import git_state

    sha, _ = git_state()
    digest, _n = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": SLEEVE, "cfg": BASE}
    bars = {s: load_4h(s) for s in SYMBOLS}
    kb = [simulate_box(bars[s], **kw) for s in SYMBOLS]
    ah = [hold_alignment(bars[s], **kw) for s in SYMBOLS]
    bh = [hold_alignment(bars[s], always=True, **kw) for s in SYMBOLS]
    v_kb, v_ah, v_bh = view(kb), view(ah), view(bh)
    all_r = [x for r in kb for x in r.r_multiples]
    entries = sum(r.entries + r.adds for r in kb)
    criteria = {
        "K1": v_kb["full"]["sharpe"] > v_ah["full"]["sharpe"],
        "K2": v_kb["full"]["mdd"] <= v_ah["full"]["mdd"],
        "K3": all(
            a["sharpe"] > b["sharpe"] for a, b in zip(v_kb["halves"], v_ah["halves"], strict=True)
        ),
        "K4": entries >= 30 and bool(all_r) and statistics.fmean(all_r) > 0,
    }

    def brief(v: dict[str, Any]) -> dict[str, Any]:
        f = v["full"]
        return {
            "sharpe": round(f["sharpe"], 3),
            "cagr": round(f["cagr"], 4),
            "mdd": round(f["mdd"], 4),
            "halves_sharpe": [round(h["sharpe"], 3) for h in v["halves"]],
            "avg_exposure": round(v["avg_exposure"], 4),
        }

    result: dict[str, Any] = {
        "commit": sha,
        "data_sha256": digest,
        "uncommitted_allowed": dirty_paths(),
        "verdict": "PASS" if all(criteria.values()) else "REJECT",
        "criteria": criteria,
        "KB": brief(v_kb),
        "AH": brief(v_ah),
        "BH": brief(v_bh),
        "entries": entries,
        "mean_R": round(statistics.fmean(all_r), 3) if all_r else None,
        "per_symbol_entries": {s: r.entries + r.adds for s, r in zip(SYMBOLS, kb, strict=True)},
        "per_symbol_mean_R": {
            s: round(statistics.fmean(r.r_multiples), 3) if r.r_multiples else None
            for s, r in zip(SYMBOLS, kb, strict=True)
        },
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        print(json.dumps(run(), ensure_ascii=False, indent=2, default=str))
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
