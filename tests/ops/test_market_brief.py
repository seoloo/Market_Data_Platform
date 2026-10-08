from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

from auto_trading.ops.market_brief import (
    Evidence,
    Holding,
    contributions,
    describe_numbers,
    parse_nasdaq100,
    parse_spy_holdings,
    pct_rank,
    render_telegram,
    top_by,
    validate_answer,
)

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xlsx(rows: list[list[str]]) -> bytes:
    strings: list[str] = []

    def idx(v: str) -> int:
        if v not in strings:
            strings.append(v)
        return strings.index(v)

    body = "".join(
        "<row>"
        + "".join(
            f'<c t="s"><v>{idx(v)}</v></c>'
            if not v.replace(".", "").isdigit()
            else f"<c><v>{v}</v></c>"
            for v in r
        )
        + "</row>"
        for r in rows
    )
    sheet = f'<worksheet xmlns="{NS}"><sheetData>{body}</sheetData></worksheet>'
    sst = (
        f'<sst xmlns="{NS}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        z.writestr("xl/sharedStrings.xml", sst)
    return buf.getvalue()


def test_parse_spy_holdings_finds_header_and_weights() -> None:
    data = _xlsx(
        [
            ["Fund Name:", "SPDR S&P 500"],
            ["Name", "Ticker", "Identifier", "Weight"],
            ["NVIDIA CORP", "NVDA", "X1", "7.5"],
            ["APPLE INC", "AAPL", "X2", "6.25"],
            ["US DOLLAR", "CASH_USD", "X3", "0.1"],
        ]
    )
    hs = parse_spy_holdings(data)
    assert [(h.symbol, round(h.weight, 4)) for h in hs] == [("NVDA", 0.075), ("AAPL", 0.0625)]
    assert top_by(hs, 1)[0].symbol == "NVDA"


def test_parse_nasdaq100_market_cap_proxy() -> None:
    payload = {
        "data": {
            "data": {
                "rows": [
                    {"symbol": "A", "companyName": "A Co", "marketCap": "3,000"},
                    {"symbol": "B", "companyName": "B Co", "marketCap": "1,000"},
                    {"symbol": "C", "companyName": "C Co", "marketCap": "NA"},
                ]
            }
        }
    }
    hs = parse_nasdaq100(payload)
    assert [(h.symbol, h.weight) for h in hs] == [("A", 0.75), ("B", 0.25)]


def test_contributions_and_rest() -> None:
    hs = [Holding("A", "", 0.1), Holding("B", "", 0.05)]
    c = contributions(hs, {"A": 0.02}, -0.01)
    assert c["rows"][1]["contrib"] is None
    assert abs(c["top_sum"] - 0.002) < 1e-12
    assert abs(c["rest"] - (-0.012)) < 1e-12
    assert pct_rank([1.0, 2.0, 3.0, 4.0], 2.5) == 50.0


def _ev() -> list[Evidence]:
    return [
        Evidence("E1", "fed", "fed_press", "Minutes of the FOMC", "x", "u"),
        Evidence("E2", "news", "cnbc", "Yields rise to 4.31%", "x", "u", "10-year yield 4.31%"),
    ]


def test_validate_answer_keeps_supported_and_drops_unsupported() -> None:
    answer = {
        "facts": [
            {"text": "FOMC 회의록이 공개됐다", "evidence": ["E1"]},
            {"text": "금리가 4.31% 로 올랐다", "evidence": ["E2"]},
            {"text": "금리가 4.99% 로 올랐다", "evidence": ["E2"]},
            {"text": "근거 없는 사실", "evidence": []},
            {"text": "없는 번호", "evidence": ["E9"]},
        ],
        "causes": [{"text": "금리 상승 부담", "evidence": ["E2"]}],
        "unknown": ["정확한 원인"],
    }
    out = validate_answer(answer, _ev(), "SPY_return_pct=-0.24\nYields rise to 4.31%")
    assert [x["text"] for x in out["facts"]] == ["FOMC 회의록이 공개됐다", "금리가 4.31% 로 올랐다"]
    assert out["facts"][0]["state"] == "공식 원문"
    assert {d["reason"] for d in out["dropped"]} == {
        "자료에 없는 숫자",
        "근거 번호 없음 · 없는 번호",
    }
    assert out["causes"][0]["state"] == "기사 · 요약만"
    assert out["unknown"] == ["정확한 원인"]
    both = validate_answer(
        {"facts": [{"text": "회의록과 금리", "evidence": ["E1", "E2"]}]}, _ev(), ""
    )
    assert both["facts"][0]["state"] == "공식 원문 / 기사 · 요약만"


def test_render_and_numbers_block() -> None:
    brief = {
        "session": "2026-10-07",
        "market": {
            "SPY": -0.0024,
            "QQQ": -0.0025,
            "vix": {"date": "2026-10-07", "close": 15.08, "change": 0.07, "pct_rank": 12.0},
        },
        "contrib": {
            "spy": contributions([Holding("A", "", 0.1)], {"A": 0.01}, -0.0024),
            "qqq": None,
        },
        "watch": {"QNDX": -0.0016, "BTQ": None},
        "after_close": [{"title": "Futures fall", "source": "x", "url": "u"}],
        "status": {"evidence": 2, "official": 1, "model": "gemini:x"},
    }
    text = render_telegram(brief)
    assert "SPY  -0.24%" in text and "VIX  15.08" in text and "BTQ  미확보" in text
    assert "나스닥 100 상위 10\n   미확보" in text
    assert "🔺 A  +1.00%  (기여 +0.10%p)" in text
    assert "마감 뒤 소식" in text and "매매 신호 아님" in text
    assert text.count("\n") > 15  # 한 줄에 하나
    nums = describe_numbers(brief)
    assert "SPY_return_pct=-0.24" in nums and "spy:A weight_pct=10.00" in nums


def test_jev_check_two_questions_record_only() -> None:
    from auto_trading.ops.market_brief import jev_check_parse, jev_check_requests

    ai = {
        "facts": [{"text": "금리가 4.31% 로 올랐다", "evidence": ["E2"]}],
        "causes": [{"text": "금리 상승 부담", "evidence": ["E9"]}],
        "counter": [],
    }
    source, data, keys = jev_check_requests(ai, _ev(), "SPY_return_pct=-0.24")
    assert keys == {"fa00": ("facts", 0), "ca00": ("causes", 0)}
    assert list(source["questions"]) == ["fa00"]  # 인용 출처가 없는 문장은 (A) 를 묻지 않는다
    assert source["questions"]["fa00"]["instructions"]["cited_sources"] == [
        "Yields rise to 4.31% — 10-year yield 4.31%"
    ]
    assert "data_block" not in str(
        source["state"]
    )  # 출처 질문에 자료표를 섞지 않는다 (/1 실패 원인)
    assert data["state"] == {"data_block": "SPY_return_pct=-0.24"} and set(
        data["questions"]
    ) == set(keys)
    out = jev_check_parse(
        ai,
        keys,
        {"answers": {"fa00": {"noul": 0.9}}},
        {"answers": {"fa00": {"noul": 0.05}, "ca00": {"noul": 0.97}}},
    )
    assert [(x["source"], x["contradiction"], x["flag"]) for x in out] == [
        (0.9, 0.05, False),
        (None, 0.97, True),
    ]
    none = jev_check_parse(ai, keys, None, {"answers": {"fa00": {"noul": 1.7}}})
    assert [x["flag"] for x in none] == [None, None]
