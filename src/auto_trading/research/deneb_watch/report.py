"""관측 가능성과 사유를 보여 주며 위험 점수·주문 결정을 만들지 않는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from auto_trading.research.deneb_watch.catalog import FACTORS, GROUPS
from auto_trading.research.deneb_watch.domain import Entry, availability, decision_timing

LABELS = {
    "OBSERVED": "수치 관측",
    "MISSING": "미확보",
    "STALE": "관측 오래됨",
    "SOURCE_FAILED": "원천/파싱 실패",
    "UNDATED_CONTEXT": "날짜 미확인 사실",
    "CONTEXT_ONLY": "맥락만",
    "PARTIAL": "일부만 확보",
}


def iso(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, UTC).isoformat()


def kst(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, ZoneInfo("Asia/Seoul")).strftime(
        "%Y-%m-%d %H:%M:%S KST"
    )


def format_value(value: str, unit: str, factor_id: str) -> str:
    number = Decimal(value)
    if unit == "USD_million":
        return (
            f"{number / 1_000_000:,.4f}조 달러"
            if abs(number) >= 1_000_000
            else f"{number / 100:,.2f}억 달러"
        )
    if unit == "percent":
        return f"{number:.2f}%"
    if unit == "percentage_points":
        return f"{number:+.3f}%p ({number * 100:+.1f}bp)"
    if unit == "ratio":
        return f"{number * 100:.2f}%" if factor_id == "interest_burden" else f"{number:.4f}배"
    return f"{number:,.2f}포인트" if unit == "index_points" else f"{number} {unit}"


def build(entries: list[Entry], *, as_of_ms: int, head_sha256: str) -> dict[str, Any]:
    from auto_trading.research.deneb_watch.domain import visible

    visible_entries = [entry for entry in entries if visible(entry, as_of_ms)]
    factors: list[dict[str, Any]] = []
    for factor in FACTORS:
        candidates = [
            entry
            for entry in visible_entries
            if entry.record.factor_id == factor.id
            and entry.record.kind in ("fact", "failure", "baseline")
        ]
        selected = candidates[-1] if candidates else None
        state = availability(selected, as_of_ms)
        record = selected.record if selected else None
        factors.append(
            {
                "id": factor.id,
                "group": factor.group,
                "title": factor.title,
                "definition": factor.definition,
                "state": state,
                "value": str(record.value) if record and record.value is not None else None,
                "unit": record.unit if record else "",
                "source": factor.source,
                "limitation": factor.limitation,
                "entry": selected.model_dump(mode="json") if selected else None,
            }
        )
    decisions = [entry for entry in visible_entries if entry.record.kind == "decision"]
    baselines = [entry for entry in visible_entries if entry.record.kind == "baseline"]
    last_action = decisions[-1].record.action if decisions else "none"
    scope_known = last_action in ("pause_new", "sell_existing", "resume")
    pending_reason = (
        (
            "행동 범위는 사용자 확인. 실제 체결일·가격·수수료·"
            "동일 시작 자산/입출금·세금·복귀 원장 미검증"
        )
        if scope_known
        else "행동 범위·시각·동일 시작 자산/입출금/비용·세금·복귀 원장 미검증"
    )
    return {
        "schema_version": "282.1",
        "as_of_ms": as_of_ms,
        "as_of_utc": iso(as_of_ms),
        "as_of_kst": kst(as_of_ms),
        "mode": "INFORMATION_ONLY",
        "trading_effect": "NONE",
        "risk_score": None,
        "journal_head_sha256": head_sha256,
        "visible_records": len(visible_entries),
        "coverage": {state: sum(item["state"] == state for item in factors) for state in LABELS},
        "factors": factors,
        "decisions": [
            {"entry": entry.model_dump(mode="json"), "timing": decision_timing(entry)}
            for entry in decisions
        ],
        "hypotheses": [
            entry.model_dump(mode="json")
            for entry in visible_entries
            if entry.record.kind == "hypothesis"
        ],
        "baselines": [entry.model_dump(mode="json") for entry in baselines],
        "comparison": {
            "status": "PENDING_MATCHED_LEDGER",
            "reported_action": last_action,
            "user_confirmed_scope": scope_known,
            "avoided_loss": None,
            "missed_upside": None,
            "incremental_pnl": None,
            "reason": pending_reason,
        },
        "new_money_g0": {
            "status": "DATA_INSUFFICIENT",
            "power_80_verified": False,
            "strategy_registered": False,
            "new_performance_calculations": 0,
            "reason": "시점 일치 행동/복귀 표본과 대응 계좌가 아직 없음",
        },
    }


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Deneb 위험지도와 중지 복귀 기록",
        "",
        f"기준 시각: {report['as_of_kst']} · 정보 전용 · 주문 영향 없음",
        "",
        "확보되지 않은 요인을 안전/중립으로 채우지 않았습니다. 아래 수치는 위험도 점수가 아닙니다.",
        "동일 사건의 여러 지표를 독립 경고로 합산하지 않습니다.",
        "",
        "## 자료 확보 상태",
        "",
        " | ".join(f"{LABELS[key]} {count}개" for key, count in report["coverage"].items()),
        "",
    ]
    for group in GROUPS:
        lines += [f"## {group}", "", "| 항목 | 확보 상태 | 관찰과 한계 |", "|---|---|---|"]
        for factor in report["factors"]:
            if factor["group"] != group:
                continue
            entry = factor["entry"]
            record = entry["record"] if entry else None
            note = record["summary"] if record else "현재 관찰값 없음"
            if factor["value"] is not None:
                note = f"{format_value(factor['value'], factor['unit'], factor['id'])}. {note}"
            if record:
                if record["observed_ms"] is not None:
                    day = datetime.fromtimestamp(
                        record["observed_ms"] / 1000, ZoneInfo("America/New_York")
                    ).date()
                    note += f". 뉴욕 관측일 {day}"
                note += f". 첫 저장 {kst(entry['recorded_ms'])}"
                if record["details"].get("counterpoint"):
                    note += f". 반대 근거/추가 확인: {record['details']['counterpoint']}"
                if record["source_url"].startswith("https://"):
                    note += f". [출처]({record['source_url']})"
            note += f". {factor['limitation']}"
            lines.append(
                f"| {_cell(factor['title'])} | {LABELS[factor['state']]} | {_cell(note)} |"
            )
        lines.append("")
    lines += ["## 사실에서 영향으로 이어지는 가설", ""]
    if not report["hypotheses"]:
        lines.append(
            "근거와 연결한 분석 가설이 아직 없습니다. 사실 개수를 위험 점수로 바꾸지 않습니다."
        )
    for entry in report["hypotheses"]:
        record = entry["record"]
        lines += [
            f"- 가설: {_cell(record['summary'])}",
            f"  근거 연결: {_cell(', '.join(record['related_keys']) or '연결 미확보')}",
        ]
    lines += ["", "## 중지와 복귀", ""]
    if not report["decisions"]:
        lines.append("아직 행동 진술 기록이 없습니다. 정지/복귀를 자동 실행하지 않습니다.")
    for decision in report["decisions"]:
        entry = decision["entry"]
        record = entry["record"]
        lines += [
            f"- {record['action']} · {decision['timing']} · 첫 기록 {iso(entry['recorded_ms'])}",
            f"  사유: {_cell(record['summary'])}",
            f"  복귀 계획: {_cell(record['reentry_plan'] or '미정 — 임의로 만들지 않음')}",
        ]
    lines += ["", "## 기존 280 대조 자료", ""]
    if not report["baselines"]:
        lines.append("기존 그림자 원장 사본 미확보.")
    for entry in report["baselines"]:
        details = entry["record"]["details"]
        lines.append(
            f"- 마지막 처리일 {details.get('last_day', '미확인')}, "
            f"납입 {details.get('paid_krw', '미확인')}원, "
            f"평가 {details.get('value_usd', '미확인')} USD. "
            "기존 보고서를 읽은 값이며 이번 수집 시각의 실시간 평가가 아닙니다."
        )
    lines += [
        "",
        "**피한 손실·놓친 수익·증분 손익: 계산 대기.** " + report["comparison"]["reason"],
        "TQQQ 한 종목의 가격 하락률을 Deneb 전체 손실 회피액으로 바꾸지 않습니다.",
        "",
        "## 신규 납입 연구 감사",
        "",
        "DATA_INSUFFICIENT · 80% 검정력 미확인 · "
        "전략 사전등록 없음 · 새 손익 계산 0회. 281의 한정 예외를 이전하지 않습니다.",
        "",
        "## 재현과 운영 경계",
        "",
        f"원장 헤드 SHA256: `{report['journal_head_sha256']}`. "
        "현재 DB의 변경 탐지이며 외부 공증이 아닙니다.",
        "원문·수집 시각·정정 이력을 별도 보존합니다. "
        "기존 전략·280·Live/Paper·예약은 변경하지 않습니다.",
        "이 실행은 수동 관찰이며 이후 자동 갱신 예약은 등록하지 않았습니다.",
    ]
    return "\n".join(lines) + "\n"


def json_text(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"
