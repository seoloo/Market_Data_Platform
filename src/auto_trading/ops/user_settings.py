"""사용자 설정 — 오르빗 설정 화면에서 바꾸는 값 (사용자 2026-10-10 시안 확정).

* 저장: ``data/settings/settings.json`` (값) · ``data/settings/changes.jsonl`` (바꾼 기록).
  파일이 없으면 기본값 = 2026-10-10 실제 운용값이라, 설정 화면을 한 번도 안 써도 동작은 같다.
* 매매 규칙(연구로 정한 값)은 여기 없다 — 화면에서 잠금으로만 보여 준다.
* 비상 정지는 따로 저장하지 않고 ``data/HALT`` 파일 그 자체다(모든 실행기가 이미 본다).
* 토스 적립 하루 금액 · 켜기/끄기는 **내일(KST)부터** 바뀐다. 바꾼 날짜와 금액을 기록에 남겨,
  적립금 계산(그날부터 새 금액)이 지난 날을 다시 계산하지 않게 한다. 끈 기간은 금액 0 이다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "ACC_SYMBOLS",
    "FIELDS",
    "Change",
    "Field",
    "acc_history",
    "apply",
    "diff",
    "get",
    "load",
    "read_log",
    "validate",
]

_KST = timedelta(hours=9)


@dataclass(frozen=True, slots=True)
class Field:
    key: str
    label: str
    kind: str
    """'int' · 'float' · 'bool' · 'enum'."""
    default: Any
    applies: str
    """언제부터 적용되나 (확인 창에 보임)."""
    lo: float | None = None
    hi: float | None = None
    options: tuple[str, ...] = ()


ACC_SYMBOLS = ("BTQ", "WQTM", "QS")
"""화면에서 하루 금액 · 켜기를 바꿀 수 있는 토스 적립 종목 (QNDX 는 QS 전략과 얽혀 대화로만)."""
_ACC_DEFAULT = {"BTQ": 1.0, "WQTM": 1.0, "QS": 1.0}

FIELDS: tuple[Field, ...] = (
    Field(
        "fire.target_krw",
        "파이어 목표",
        "int",
        100_000_000,
        "다음 '현황' 답 · 다음 보고부터",
        10_000_000,
        10_000_000_000,
    ),
    Field("fire.report_on", "매달 파이어 보고", "bool", True, "다음 보고(매달 1일)부터"),
    Field("fire.rate_good", "도달 예상 · 좋을 때", "float", 20.0, "다음 보고부터", 0, 50),
    Field("fire.rate_mid", "도달 예상 · 보통", "float", 12.0, "다음 보고부터", 0, 50),
    Field("fire.rate_bad", "도달 예상 · 나쁠 때", "float", 4.0, "다음 보고부터", -20, 50),
    Field(
        "glide.monthly_krw",
        "Glide 기본 월 납입",
        "int",
        300_000,
        "다음 매수 달부터 (텔레그램 질문의 기본값)",
        0,
        5_000_000,
    ),
    Field(
        "glide.max_krw",
        "Glide 한 달 상한",
        "int",
        5_000_000,
        "다음 텔레그램 답부터",
        100_000,
        50_000_000,
    ),
    Field("glide.ask_on", "매수 전날 금액 묻기", "bool", True, "다음 매수 달부터"),
    Field("glide.harvest_on", "12월 양도세 공제 실현", "bool", True, "올해 12월 공지부터"),
    Field("alerts.cruise_trade", "Cruise 매매 알림", "bool", True, "다음 09:05 실행부터"),
    Field("alerts.cruise_weekly", "Cruise 주간 요약", "bool", True, "다음 토요일부터"),
    Field("alerts.fx", "환전 알림", "bool", True, "다음 환전 확인(평일 09 · 12 · 15시)부터"),
    Field("alerts.bot_down", "봇 멈춤 알림", "bool", True, "바로"),
    Field("alerts.theme", "주간 테마", "bool", True, "다음 토요일부터"),
    Field("alerts.brief", "시장 브리핑", "bool", True, "다음 브리핑부터"),
    Field("alerts.news", "종목 뉴스 답", "bool", True, "바로"),
    Field(
        "screen.theme",
        "테마",
        "enum",
        "system",
        "화면을 새로 열 때부터",
        options=("system", "light", "dark"),
    ),
    Field("screen.hide_amounts", "금액 가리고 시작", "bool", False, "화면을 새로 열 때부터"),
    *(
        Field(
            f"acc.{s}.usd", f"{s} 하루 금액", "float", _ACC_DEFAULT[s], "내일 적립분부터", 0.5, 20
        )
        for s in ACC_SYMBOLS
    ),
    *(Field(f"acc.{s}.on", f"{s} 적립", "bool", True, "내일 적립분부터") for s in ACC_SYMBOLS),
    Field("safety.halt", "비상 정지", "bool", False, "바로 (모든 새 주문)"),
)
_BY_KEY = {f.key: f for f in FIELDS}


def _paths(root: Path) -> tuple[Path, Path, Path]:
    folder = root / "data" / "settings"
    return folder / "settings.json", folder / "changes.jsonl", root / "data" / "HALT"


def _read(root: Path) -> dict[str, Any]:
    try:
        d = json.loads(_paths(root)[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def load(root: Path) -> dict[str, Any]:
    """키 → 값 (저장 안 된 것은 기본값). ``safety.halt`` 는 HALT 파일이 있는지."""
    saved = _read(root).get("values") or {}
    out = {f.key: saved.get(f.key, f.default) for f in FIELDS}
    out["safety.halt"] = _paths(root)[2].exists()
    return out


def get(root: Path, key: str) -> Any:
    return load(root)[key]


def acc_history(root: Path, symbol: str) -> list[tuple[str, float]]:
    """그 종목의 (그날부터, 하루 금액) 기록 — 끈 기간은 0. ``Plan.changes`` 뒤에 붙인다."""
    raw = (_read(root).get("acc_history") or {}).get(symbol) or []
    return [(str(d), float(r)) for d, r in raw]


def _parse(f: Field, raw: str | None) -> Any:
    if f.kind == "bool":
        return raw in ("on", "1", "true")
    text = (raw or "").replace(",", "").replace("원", "").replace("$", "").strip()
    if f.kind == "enum":
        if text not in f.options:
            raise ValueError("고를 수 없는 값")
        return text
    try:
        num = float(text)
    except ValueError:
        raise ValueError("숫자로 적어 주세요") from None
    if (f.lo is not None and num < f.lo) or (f.hi is not None and num > f.hi):
        unit = "$" if f.key.startswith("acc.") else ""
        raise ValueError(f"{unit}{f.lo:,g} ~ {unit}{f.hi:,g} 사이로 적어 주세요")
    return round(num) if f.kind == "int" else round(num, 2)


def validate(
    form: Mapping[str, str], current: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """폼 → (새 값, 키별 오류). 폼에 없는 bool 은 꺼짐(체크 안 한 칸), 그 밖에 없는 칸은 그대로."""
    new = dict(current)
    errors: dict[str, str] = {}
    for f in FIELDS:
        if f.kind == "bool":
            new[f.key] = _parse(f, form.get(f.key))
            continue
        if f.key not in form:
            continue
        try:
            new[f.key] = _parse(f, form[f.key])
        except ValueError as exc:
            errors[f.key] = str(exc)
    if not errors:
        if not new["fire.rate_good"] >= new["fire.rate_mid"] >= new["fire.rate_bad"]:
            errors["fire.rate_mid"] = "좋을 때 ≥ 보통 ≥ 나쁠 때 순서로"
        if new["glide.monthly_krw"] > new["glide.max_krw"]:
            errors["glide.monthly_krw"] = "한 달 상한보다 클 수 없어요"
    return new, errors


@dataclass(frozen=True, slots=True)
class Change:
    key: str
    label: str
    before: Any
    after: Any
    applies: str


def show(key: str, value: Any) -> str:
    f = _BY_KEY[key]
    if f.kind == "bool":
        return "켜짐" if value else "꺼짐"
    if f.kind == "enum":
        return {"system": "시스템", "light": "라이트", "dark": "다크"}.get(str(value), str(value))
    if key.startswith("acc."):
        return f"${float(value):g}"
    if key.startswith("fire.rate"):
        return f"연 {float(value):g}%"
    return f"{float(value):,.0f}원"


def diff(current: Mapping[str, Any], new: Mapping[str, Any]) -> list[Change]:
    return [
        Change(f.key, f.label, current[f.key], new[f.key], f.applies)
        for f in FIELDS
        if current.get(f.key) != new.get(f.key)
    ]


def _today_kst(now: datetime) -> str:
    return (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d")


def apply(root: Path, changes: Sequence[Change], now: datetime, by: str = "설정 화면") -> None:
    """저장 · 기록 · 비상 정지 파일 · 적립 금액 기록. 바뀐 것이 없으면 아무것도 안 한다."""
    if not changes:
        return
    path, log, halt = _paths(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = _read(root)
    values = dict(d.get("values") or {})
    hist = {k: list(v) for k, v in (d.get("acc_history") or {}).items()}
    after = {**load(root), **{c.key: c.after for c in changes}}
    tomorrow = (datetime.fromisoformat(_today_kst(now)) + timedelta(days=1)).strftime("%Y-%m-%d")
    for c in changes:
        if c.key == "safety.halt":
            if c.after:
                halt.write_text(f"설정 화면에서 켬 {_today_kst(now)}\n", encoding="utf-8")
            else:
                halt.unlink(missing_ok=True)
            continue
        values[c.key] = c.after
    for s in ACC_SYMBOLS:
        if any(c.key.startswith(f"acc.{s}.") for c in changes):
            rate = float(after[f"acc.{s}.usd"]) if after[f"acc.{s}.on"] else 0.0
            rows = [r for r in hist.get(s, []) if r[0] != tomorrow]
            hist[s] = [*rows, [tomorrow, rate]]
    d.update({"values": values, "acc_history": hist})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    stamp = (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d %H:%M")
    with log.open("a", encoding="utf-8") as fh:
        for c in changes:
            fh.write(
                json.dumps(
                    {
                        "at": stamp,
                        "by": by,
                        "key": c.key,
                        "what": f"{c.label} {show(c.key, c.before)} → {show(c.key, c.after)}",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def read_log(root: Path, limit: int = 30) -> list[dict[str, str]]:
    try:
        lines = _paths(root)[1].read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit * 3 :]):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append({k: str(v) for k, v in row.items()})
    return out[:limit]
