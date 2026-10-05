"""실적 개선형 주도주 Phase 0 — 종료(상장폐지) 3 종목의 Tiingo 무료 가격 소표본 (조회만).

사용 예::

    python -m scripts.audit_tiingo_terminal_sample

Codex 인계(docs/research/IDEAS.md 4 번 보충 · FUNDAMENTAL_FREE_HISTORY_FEASIBILITY §5-1) 그대로:
TWTR·CTXS·NLSN 최대 3 종목의 metadata 와 종료 부근(2022-09-01 ~ 11-15) 일별 가격만 요청한다.
요청 6 번(무료 Starter 한도: 시간당 50). 키는 요청 머리말(Authorization)로만 보내고
주소·출력·파일에 남기지 않는다. 원응답은 ``data/research/fundamental_pilot/`` (git 제외)에
덮어쓰지 않고 저장하고, 해시를 manifest 에 남긴다.
수익 계산·종목 선정·대량 수집은 하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "research" / "fundamental_pilot"
MANIFEST = REPO / "docs" / "research" / "fundamental-tiingo-sample-manifest.json"
BASE = "https://api.tiingo.com/tiingo/daily"
SYMBOLS = ("TWTR", "CTXS", "NLSN")
START, END = "2022-09-01", "2022-11-15"
STAMP = "20261005"
KNOWN: dict[str, dict[str, str | None]] = {
    "TWTR": {"last_trade": "2022-10-27", "halt": "2022-10-28"},
    "CTXS": {"last_trade": "2022-09-29", "halt": None},
    "NLSN": {"last_trade": None, "halt": None},
}
"""이미 확인한 사실(문서 136·종료 계약·무료 경로 감사). NLSN 은 아직 원문 대조 전."""


def summarize(
    symbol: str, meta: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """가격 응답 요약 (순수). 마지막 거래 뒤 행·거래량 0·수정가 차이·배당/분할을 센다."""
    known = KNOWN.get(symbol, {})
    dates = [str(r.get("date", ""))[:10] for r in rows]
    last_trade = known.get("last_trade")
    after = [r for r, d in zip(rows, dates, strict=True) if last_trade and d > last_trade]
    adj_diff = [
        d
        for r, d in zip(rows, dates, strict=True)
        if r.get("close")
        and r.get("adjClose")
        and abs(float(r["adjClose"]) - float(r["close"])) > 1e-9
    ]
    return {
        "symbol": symbol,
        "meta_start": meta.get("startDate"),
        "meta_end": meta.get("endDate"),
        "rows": len(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "known_last_trade": last_trade,
        "rows_after_known_last_trade": [
            {
                "date": str(r.get("date", ""))[:10],
                "close": r.get("close"),
                "volume": r.get("volume"),
            }
            for r in after
        ],
        "zero_volume_rows": sum(1 for r in rows if not r.get("volume")),
        "adj_differs_rows": len(adj_diff),
        "div_cash_rows": sum(1 for r in rows if float(r.get("divCash") or 0) != 0),
        "split_rows": sum(1 for r in rows if float(r.get("splitFactor") or 1) != 1),
        "last_rows": [
            {
                k: r.get(k)
                for k in ("date", "open", "high", "low", "close", "volume", "adjClose", "divCash")
            }
            for r in rows[-3:]
        ],
    }


def _save(name: str, body: bytes) -> dict[str, Any]:
    path = RAW / name
    if path.exists():
        raise SystemExit(f"덮어쓰지 않는다: {path}")
    path.write_bytes(body)
    return {
        "file": f"data/research/fundamental_pilot/{name}",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def run() -> dict[str, Any]:
    import httpx

    from auto_trading.config.secrets import read_env_file

    key = read_env_file(REPO / ".env").get("TIINGO_API_KEY", "")
    if not key:
        raise SystemExit("TIINGO_API_KEY 가 .env 에 없다")
    if MANIFEST.exists():
        raise SystemExit(f"이미 실행했다: {MANIFEST}")
    head = {"Authorization": f"Token {key}", "Content-Type": "application/json"}
    files: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    fetched = datetime.now(UTC).isoformat(timespec="seconds")
    with httpx.Client(headers=head, timeout=30) as client:
        for sym in SYMBOLS:
            got: dict[str, Any] = {}
            for kind, url, params in (
                ("meta", f"{BASE}/{sym}", None),
                ("prices", f"{BASE}/{sym}/prices", {"startDate": START, "endDate": END}),
            ):
                resp = client.get(url, params=params)
                rec = _save(f"tiingo-{sym.lower()}-{kind}-{STAMP}.json", resp.content)
                rec.update(
                    {
                        "symbol": sym,
                        "kind": kind,
                        "url": url,  # 키는 머리말로만 보냈다 — 주소에 없음
                        "params": params,
                        "http_status": resp.status_code,
                        "fetched_at": fetched,
                    }
                )
                files.append(rec)
                try:
                    got[kind] = resp.json()
                except ValueError:
                    got[kind] = None
            meta = got.get("meta") if isinstance(got.get("meta"), dict) else {}
            rows = got.get("prices") if isinstance(got.get("prices"), list) else []
            summary.append(summarize(sym, meta or {}, rows or []))
    out = {
        "purpose": (
            "실적 개선형 주도주 Phase 0 — 종료 3 종목 Tiingo 무료 가격 소표본"
            " (가격 제공 여부·정지 뒤 행·raw/수정)"
        ),
        "window": [START, END],
        "files": files,
        "summary": summary,
        "note": "원응답은 git 제외 로컬 파일. 재배포하지 않는다. 키·계정 정보는 남기지 않았다.",
    }
    MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv:
        raise SystemExit("usage: python -m scripts.audit_tiingo_terminal_sample")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
