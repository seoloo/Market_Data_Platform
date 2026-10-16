"""국내 실적 일정(DART) — IR 안내공시 확정 · 이번 분기 끝 · 지난해 기준 예상 · 모름 (가짜 자료)."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ops import kr_earnings as m

IR_TEXT = (
    "SK하이닉스/기업설명회(IR) 개최(안내공시) 1. 일시 및 장소 일시 2026-10-28 09:00 장소 - "
    "2. 참가 대상자 투자자 및 언론사 3. 개최목적 2026년 3분기 경영실적 발표 "
    "4. 개최방법 Conference Call"
)


def _row(d: str, name: str, no: str = "1") -> dict[str, str]:
    return {"rcept_dt": d, "report_nm": name, "rcept_no": no}


def test_parse_ir_date_and_purpose() -> None:
    assert m.parse_ir(IR_TEXT) == (date(2026, 10, 28), "2026년 3분기 경영실적 발표")
    assert m.parse_ir("아무 글") == (None, "")


def test_quarterly_releases_skip_monthly_sales_and_corrections() -> None:
    hyundai = [
        _row("20261001", "영업(잠정)실적(공정공시)"),  # 월별 판매
        _row("20260723", "연결재무제표기준영업(잠정)실적(공정공시)"),
        _row("20260901", "영업(잠정)실적(공정공시)"),
        _row("20260730", "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"),
        _row("20251030", "연결재무제표기준영업(잠정)실적(공정공시)"),
    ]
    assert m.quarterly_releases(hyundai) == [date(2025, 10, 30), date(2026, 7, 23)]
    monthly_only = [_row(f"2026{k:02d}01", "영업(잠정)실적(공정공시)") for k in range(1, 10)]
    assert m.quarterly_releases(monthly_only) == []


def test_judge_order() -> None:
    today = date(2026, 10, 16)
    rel = [date(2025, 10, 30), date(2026, 7, 23)]
    c = m.judge(today, [(date(2026, 10, 28), "3분기 경영실적 발표")], rel)
    assert (c.kind, c.day) == ("confirmed", date(2026, 10, 28))
    nd = m.judge(today, [(date(2026, 10, 28), "해외 NDR")], rel)  # 실적 아닌 IR 은 무시
    assert (nd.kind, nd.day) == ("estimated", date(2026, 10, 30))
    done = m.judge(today, [], [date(2026, 10, 8)])
    assert done.kind == "done" and "10/08" in done.note
    assert m.judge(today, [], [date(2026, 7, 23)]).kind == "unknown"


def test_events_block_within_three_trading_days() -> None:
    days = [date(2026, 10, 26), date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
    near = m.kr_company_events(m.KrEarnings(date(2026, 10, 28), "confirmed", "IR"), days)
    assert not near.ok and near.earnings_in_days == 2 and "IR 공시 확정" in near.blocked[0]
    far = m.kr_company_events(
        m.KrEarnings(date(2026, 11, 5), "estimated", "x"), days[:1] + days[1:]
    )
    assert far.ok and far.earnings_date == "2026-11-05" and far.earnings_in_days == 8
    done = m.kr_company_events(m.KrEarnings(None, "done", "이번 분기 실적 발표 끝 (10/08)"), days)
    assert done.ok and "석 달쯤 뒤" in done.lines[0]
    unknown = m.kr_company_events(m.KrEarnings(None, "unknown", "못 찾음"), days)
    assert not unknown.ok


class _Resp:
    def __init__(self, data: Any = None, content: bytes = b"") -> None:
        self._data, self.content = data, content

    def json(self) -> Any:
        return self._data


def _doc_zip(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("doc.xml", f"<DOC><P>{text}</P></DOC>")
    return buf.getvalue()


def test_lookup_fetches_ir_doc_and_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "corp_code", lambda root, stock: "00164779")
    calls: list[str] = []

    def get(url: str, params: dict[str, str]) -> _Resp:
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("list.json"):
            return _Resp(
                {
                    "status": "000",
                    "total_page": 1,
                    "list": [
                        _row("20261014", "기업설명회(IR)개최(안내공시)", "R1"),
                        _row("20260729", "연결재무제표기준영업(잠정)실적(공정공시)"),
                    ],
                }
            )
        return _Resp(content=_doc_zip(IR_TEXT))

    r = m.lookup(tmp_path, "000660", date(2026, 10, 16), "k", get=get, now_s=1000.0)
    assert (r.kind, r.day) == ("confirmed", date(2026, 10, 28))
    assert calls == ["list.json", "document.xml"]
    again = m.lookup(tmp_path, "000660", date(2026, 10, 16), "k", get=get, now_s=2000.0)
    assert again == r and len(calls) == 2  # 6 시간 캐시
