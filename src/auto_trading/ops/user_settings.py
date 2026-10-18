"""사용자 설정 — 오르빗 설정 화면에서 바꾸는 값 (사용자 2026-10-10 시안 확정).

* 저장: ``data/settings/settings.json`` (값) · ``data/settings/changes.jsonl`` (바꾼 기록).
  파일이 없으면 기본값 = 2026-10-10 실제 운용값이라, 설정 화면을 한 번도 안 써도 동작은 같다.
* 매매 규칙(연구로 정한 값)은 여기 없다 — 화면에서 잠금으로만 보여 준다.
* 비상 정지는 따로 저장하지 않고 ``data/HALT`` 파일 그 자체다(모든 실행기가 이미 본다).
* 종목 추가 · 빼기(2026-10-10): 미국은 기호, 국내는 이름 · 코드. 토스 종목 목록 파일
  (``data/toss/universe.json``, 키 없이 읽음)에서 찾은 것만 받는다. 합쳐서 최대 8 종목.
  추가 종목의 값 키는 ``x.<US|KR>.<기호>.daily|on|removed`` 다.
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

MAX_ITEMS = 8
"""토스 적립 종목 수 상한 (미국 + 국내, QNDX 빼고)."""

__all__ = [
    "ACC_SYMBOLS",
    "FIELDS",
    "MAX_ITEMS",
    "Change",
    "Field",
    "acc_history",
    "acc_items",
    "active_count",
    "add_item",
    "apply",
    "diff",
    "extras",
    "fields",
    "get",
    "load",
    "read_log",
    "remove_item",
    "show",
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
        "deneb.monthly_krw",
        "Deneb 기본 월 납입",
        "int",
        300_000,
        "다음 매수 달부터 (텔레그램 질문의 기본값)",
        0,
        5_000_000,
    ),
    Field(
        "deneb.max_krw",
        "Deneb 한 달 상한",
        "int",
        5_000_000,
        "다음 텔레그램 답부터",
        100_000,
        50_000_000,
    ),
    Field("deneb.ask_on", "매수 전날 금액 묻기", "bool", True, "다음 매수 달부터"),
    Field("deneb.harvest_on", "12월 양도세 공제 실현", "bool", True, "올해 12월 공지부터"),
    Field("alerts.vega_trade", "Vega 매매 알림", "bool", True, "다음 09:05 실행부터"),
    Field("alerts.vega_weekly", "Vega 주간 요약", "bool", True, "다음 토요일부터"),
    Field("alerts.fx", "환전 알림", "bool", True, "다음 환전 확인(평일 09 · 12 · 15시)부터"),
    Field("alerts.bot_down", "봇 멈춤 알림", "bool", True, "바로"),
    Field("alerts.theme", "주간 테마", "bool", True, "다음 토요일부터"),
    Field("alerts.brief", "시장 브리핑", "bool", True, "다음 브리핑부터"),
    Field("alerts.news", "종목 뉴스 답", "bool", True, "바로"),
    Field("alerts.events", "경제 일정 · 실적 경고", "bool", True, "바로"),
    Field("alerts.paper", "매일 Paper 요약 (21:30)", "bool", True, "다음 21:30 부터"),
    Field("demo.altair", "Altair 선물 데모 (가짜 돈, 옛 AG4S2)", "bool", True, "다음 4 시간봉부터"),
    Field(
        "stock.orders",
        "개별주 비중 주문 (텔레그램 💹 개별주 · [이대로 매수] 를 누를 때만)",
        "bool",
        True,
        "바로",
    ),
    Field(
        "stock.reco",
        "💹 월간 미장 주도주 추천 · 매도 신호 (월말 종가, 버튼 누를 때만 주문)",
        "bool",
        True,
        "다음 07:30 확인부터",
    ),
    Field(
        "stock.pead",
        "PEAD 실적 갭 10%+ 매수 알림 (💹 약 $100 · 손절 없이 20 거래일, 버튼 누를 때만)",
        "bool",
        True,
        "다음 PEAD 실행(화 ~ 토 21:40)부터",
    ),
    Field(
        "vega.earn",
        "Vega 남는 USDT 를 바이낸스 유동형(Simple Earn)에 — 살 때 먼저 꺼냄",
        "bool",
        False,
        "다음 Vega 실행 · 입금 확인부터",
    ),
    Field(
        "live.altair",
        "Altair 실거래 (바이낸스 선물 USDC) — .env 주문 허용도 켜야 주문",
        "bool",
        False,
        "다음 4 시간봉부터",
    ),
    Field(
        "live.sirius",
        "✨ Sirius 실거래 (토스 소수점 · 미장 주도주 10 종목 6 달 보유)"
        " — .env 토스 주문 허용도 켜야 주문",
        "bool",
        False,
        "다음 Sirius 실행(미국 정규장 23:45 · 02:00 KST)부터",
    ),
    Field(
        "polaris.on",
        "🧭 Polaris 나무 안정 적립 (매달 버튼 누를 때만 주문)",
        "bool",
        True,
        "다음 09:00 확인부터",
    ),
    Field(
        "polaris.monthly_krw",
        "🧭 Polaris 한 회차 금액 (원)",
        "int",
        1_000_000,
        "다음 회차 버튼부터",
        100_000,
        3_000_000,
    ),
    Field(
        "namu.follow",
        "나무 모의 — 국장 수급 Paper 따라가기",
        "bool",
        True,
        "다음 거래일 15:00 부터",
    ),
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
    *(
        Field(
            f"acc.{s}.removed",
            f"{s} 목록에서 빼기",
            "bool",
            False,
            "내일 적립분부터 (보유는 그대로)",
        )
        for s in ACC_SYMBOLS
    ),
    Field("safety.halt", "비상 정지", "bool", False, "바로 (모든 새 주문)"),
)


def _paths(root: Path) -> tuple[Path, Path, Path]:
    folder = root / "data" / "settings"
    return folder / "settings.json", folder / "changes.jsonl", root / "data" / "HALT"


def _read(root: Path) -> dict[str, Any]:
    try:
        d = json.loads(_paths(root)[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def extras(root: Path) -> list[dict[str, Any]]:
    """추가한 종목 (뺀 것도 ``removed`` 날짜와 함께 남는다 — 보유 기록을 잇기 위해)."""
    raw = _read(root).get("extras") or []
    return [dict(x) for x in raw if isinstance(x, dict) and x.get("sym") and x.get("mkt")]


def _xkey(x: Mapping[str, Any]) -> str:
    return f"x.{x['mkt']}.{x['sym']}"


def _xfields(meta: Sequence[Mapping[str, Any]]) -> list[Field]:
    out = []
    for x in meta:
        if x.get("removed"):
            continue
        k, name = _xkey(x), f"{x.get('name') or x['sym']} ({x['sym']})"
        kr = x["mkt"] == "KR"
        out += [
            Field(
                f"{k}.daily",
                f"{name} 하루 금액",
                "int" if kr else "float",
                float(x.get("daily0") or (5_000 if kr else 1.0)),
                "내일 적립분부터",
                500 if kr else 0.5,
                30_000 if kr else 20,
            ),
            Field(f"{k}.on", f"{name} 적립", "bool", True, "내일 적립분부터"),
            Field(
                f"{k}.removed",
                f"{name} 목록에서 빼기",
                "bool",
                False,
                "내일 적립분부터 (보유는 그대로)",
            ),
        ]
    return out


def fields(meta: Sequence[Mapping[str, Any]]) -> list[Field]:
    return [*FIELDS, *_xfields(meta)]


_OLD_KEYS = {
    "deneb.monthly_krw": "glide.monthly_krw",
    "deneb.max_krw": "glide.max_krw",
    "deneb.ask_on": "glide.ask_on",
    "deneb.harvest_on": "glide.harvest_on",
    "alerts.vega_trade": "alerts.cruise_trade",
    "alerts.vega_weekly": "alerts.cruise_weekly",
    "demo.altair": "demo.ag4s2",
}
"""2026-10-15 이름 변경 전 키 — 저장된 옛 값이 있으면 그대로 쓴다."""


def load(root: Path) -> dict[str, Any]:
    """키 → 값 (저장 안 된 것은 기본값). ``safety.halt`` 는 HALT 파일이 있는지.

    ``_extras`` 에 추가 종목 정보(이름 · 시장)를 함께 싣는다 — 화면 · 검사 · 기록이 같이 쓴다."""
    saved = _read(root).get("values") or {}
    meta = extras(root)
    out: dict[str, Any] = {
        f.key: saved.get(f.key, saved.get(_OLD_KEYS.get(f.key, ""), f.default))
        for f in fields(meta)
    }
    out["safety.halt"] = _paths(root)[2].exists()
    out["_extras"] = meta
    return out


def get(root: Path, key: str) -> Any:
    return load(root)[key]


def acc_history(root: Path, symbol: str) -> list[tuple[str, float]]:
    """그 종목의 (그날부터, 하루 금액) 기록 — 끈 · 뺀 기간은 0. ``Plan.changes`` 뒤에 붙인다."""
    raw = (_read(root).get("acc_history") or {}).get(symbol) or []
    return [(str(d), float(r)) for d, r in raw]


def _parse(f: Field, raw: str | None) -> Any:
    if f.kind == "bool":
        return raw in ("on", "1", "true")
    text = (raw or "").replace(",", "").replace("원", "").replace("$", "")
    text = text.replace("₩", "").replace("\\", "").strip()
    if f.kind == "enum":
        if text not in f.options:
            raise ValueError("고를 수 없는 값")
        return text
    try:
        num = float(text)
    except ValueError:
        raise ValueError("숫자로 적어 주세요") from None
    if (f.lo is not None and num < f.lo) or (f.hi is not None and num > f.hi):
        if f.key.startswith(("acc.", "x.US.")):
            raise ValueError(f"${f.lo:,g} ~ ${f.hi:,g} 사이로 적어 주세요")
        if f.key.startswith("x.KR."):
            raise ValueError(f"₩{f.lo:,g} ~ ₩{f.hi:,g} 사이로 적어 주세요")
        raise ValueError(f"{f.lo:,g} ~ {f.hi:,g} 사이로 적어 주세요")
    return round(num) if f.kind == "int" else round(num, 2)


def active_count(values: Mapping[str, Any]) -> int:
    """목록에 있는 적립 종목 수 (뺀 것 빼고)."""
    built = sum(1 for s in ACC_SYMBOLS if not values.get(f"acc.{s}.removed"))
    added = sum(
        1
        for x in values.get("_extras") or []
        if not x.get("removed") and not values.get(f"{_xkey(x)}.removed")
    )
    return built + added


def validate(
    form: Mapping[str, str],
    current: Mapping[str, Any],
    universe: Sequence[Any] = (),
) -> tuple[dict[str, Any], dict[str, str]]:
    """폼 → (새 값, 키별 오류). 폼에 없는 bool 은 꺼짐(체크 안 한 칸), 그 밖에 없는 칸은 그대로.

    ``new.query`` 가 있으면 ``universe`` (ops.kr_accumulate.Listed 목록)에서 찾아 추가한다."""
    new = dict(current)
    meta = [dict(x) for x in current.get("_extras") or []]
    new["_extras"] = meta
    errors: dict[str, str] = {}
    for f in fields(meta):
        if f.key == "safety.halt":
            continue  # 비상 정지는 전용 버튼 · 확인 창으로만 (큰 폼 저장이 끄지 못하게)
        if f.kind == "bool":
            new[f.key] = _parse(f, form.get(f.key))
            continue
        if f.key not in form:
            continue
        try:
            new[f.key] = _parse(f, form[f.key])
        except ValueError as exc:
            errors[f.key] = str(exc)
    query = (form.get("new.query") or "").strip()
    if query:
        _add(form, query, new, meta, universe, errors)
    if not errors:
        if not new["fire.rate_good"] >= new["fire.rate_mid"] >= new["fire.rate_bad"]:
            errors["fire.rate_mid"] = "좋을 때 ≥ 보통 ≥ 나쁠 때 순서로"
        if new["deneb.monthly_krw"] > new["deneb.max_krw"]:
            errors["deneb.monthly_krw"] = "한 달 상한보다 클 수 없어요"
    return new, errors


def _add(
    form: Mapping[str, str],
    query: str,
    new: dict[str, Any],
    meta: list[dict[str, Any]],
    universe: Sequence[Any],
    errors: dict[str, str],
) -> None:
    from auto_trading.ops.kr_accumulate import find

    mkt = "KR" if form.get("new.mkt") == "KR" else "US"
    if not universe:
        errors["new.query"] = "토스 종목 목록을 아직 못 받았어요 — 잠시 뒤 다시"
        return
    hits = find(query, universe, mkt)
    if len(hits) != 1:
        errors["new.query"] = (
            "찾지 못했어요 — 기호(미국) · 이름 또는 6 자리 코드(국내)로"
            if not hits
            else "여러 개가 맞아요: " + " · ".join(f"{h.name}({h.symbol})" for h in hits)
        )
        return
    hit = hits[0]
    if hit.symbol == "QNDX":
        errors["new.query"] = "QNDX 는 QS 전략과 얽혀 대화로만 바꿔요"
        return
    if mkt == "US" and hit.symbol in ACC_SYMBOLS:
        if new.get(f"acc.{hit.symbol}.removed"):
            new[f"acc.{hit.symbol}.removed"] = False  # 뺐던 기본 종목을 다시 넣기
            return
        errors["new.query"] = f"{hit.symbol} 는 이미 목록에 있어요"
        return
    if any(x["sym"] == hit.symbol and not x.get("removed") for x in meta):
        errors["new.query"] = f"{hit.name}({hit.symbol}) 는 이미 목록에 있어요"
        return
    if active_count(new) >= MAX_ITEMS:
        errors["new.query"] = f"종목은 합쳐서 {MAX_ITEMS} 개까지예요 — 하나를 빼고 추가해 주세요"
        return
    x: dict[str, Any] = {
        "mkt": mkt,
        "sym": hit.symbol,
        "name": hit.name,
        "market": hit.market,
        "kind": hit.kind,
    }
    f_daily = _xfields([x])[0]
    try:
        daily = _parse(f_daily, form.get("new.daily") or str(f_daily.default))
    except ValueError as exc:
        errors["new.daily"] = str(exc)
        return
    x["daily0"] = daily
    meta[:] = [m for m in meta if m["sym"] != hit.symbol]  # 뺐던 종목을 다시 넣으면 새로
    meta.append(x)
    k = _xkey(x)
    new.update({f"{k}.daily": daily, f"{k}.on": True, f"{k}.removed": False, f"{k}.added": True})


def acc_items(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """지금 적립 목록(뺀 것 빼고) — 텔레그램 '적립' 메뉴 · 정리 (2026-10-18).

    {key: 설정 키 머리, sym, name, mkt: US · KR, daily: 하루 금액(달러 · 원), on}."""
    out: list[dict[str, Any]] = []
    for s in ACC_SYMBOLS:
        if values.get(f"acc.{s}.removed"):
            continue
        out.append(
            {"key": f"acc.{s}", "sym": s, "name": s, "mkt": "US",
             "daily": float(values.get(f"acc.{s}.usd", _ACC_DEFAULT[s])),
             "on": bool(values.get(f"acc.{s}.on", True))}
        )  # fmt: skip
    for x in values.get("_extras") or []:
        k = _xkey(x)
        if x.get("removed") or values.get(f"{k}.removed"):
            continue
        out.append(
            {"key": k, "sym": str(x["sym"]), "name": str(x.get("name") or x["sym"]),
             "mkt": str(x["mkt"]), "daily": float(values.get(f"{k}.daily", x.get("daily0") or 0)),
             "on": bool(values.get(f"{k}.on", True))}
        )  # fmt: skip
    return out


def add_item(
    current: Mapping[str, Any], query: str, mkt: str, daily: str, universe: Sequence[Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """설정 화면 '추가' 와 같은 검사로 한 종목 추가 → (새 값, 오류). ``daily`` 빈 값이면 기본."""
    new = dict(current)
    meta = [dict(x) for x in current.get("_extras") or []]
    new["_extras"] = meta
    errors: dict[str, str] = {}
    form = {"new.mkt": "KR" if mkt == "KR" else "US", "new.daily": daily}
    _add(form, query, new, meta, universe, errors)
    return new, errors


def remove_item(current: Mapping[str, Any], key: str) -> dict[str, Any]:
    """설정 화면 '삭제' 와 같음 — ``{key}.removed`` 를 켠다(내일 적립분부터, 보유는 그대로)."""
    flag = f"{key}.removed"
    if flag not in current or current[flag]:
        raise ValueError(f"적립 목록에 없는 종목: {key}")
    return {**current, flag: True}


@dataclass(frozen=True, slots=True)
class Change:
    key: str
    label: str
    before: Any
    after: Any
    applies: str


def show(key: str, value: Any) -> str:
    if value is None:
        return "없음"
    if key.endswith(".removed"):
        return "뺌" if value else "목록에 있음"
    if key.endswith(".added"):
        return "추가" if value else "없음"
    if key.endswith(".on") or isinstance(value, bool):
        return "켜짐" if value else "꺼짐"
    if key == "screen.theme":
        return {"system": "시스템", "light": "라이트", "dark": "다크"}.get(str(value), str(value))
    if key.startswith(("acc.", "x.US.")):
        return f"${float(value):g}"
    if key.startswith("x.KR."):
        return f"₩{float(value):,.0f}"
    if key.startswith("fire.rate"):
        return f"연 {float(value):g}%"
    return f"{float(value):,.0f}원"


def diff(current: Mapping[str, Any], new: Mapping[str, Any]) -> list[Change]:
    out = []
    meta = new.get("_extras") or []
    for x in meta:
        k = _xkey(x)
        if new.get(f"{k}.added"):
            amount = new[k + ".daily"]
            unit = f"₩{amount:,.0f}" if x["mkt"] == "KR" else f"${amount:g}"
            where = "국내" if x["mkt"] == "KR" else "미국"
            out.append(
                Change(
                    f"{k}.added",
                    f"{where} {x['name']}({x['sym']}) 추가 · 하루 {unit}",
                    None,
                    True,
                    "내일 적립분부터",
                )
            )
    for f in fields(meta):
        if f.key.startswith("x.") and new.get(f.key.rsplit(".", 1)[0] + ".added"):
            continue  # 새로 넣은 종목은 위의 '추가' 한 줄로
        if current.get(f.key, f.default) != new.get(f.key, f.default):
            out.append(Change(f.key, f.label, current.get(f.key, f.default), new[f.key], f.applies))
    return out


def _today_kst(now: datetime) -> str:
    return (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d")


def apply(
    root: Path,
    changes: Sequence[Change],
    now: datetime,
    by: str = "설정 화면",
    new: Mapping[str, Any] | None = None,
) -> None:
    """저장 · 기록 · 비상 정지 파일 · 적립 금액 기록. 바뀐 것이 없으면 아무것도 안 한다.

    ``new`` 는 검사를 통과한 새 값 전체(추가 종목 정보 ``_extras`` 포함)."""
    if not changes:
        return
    path, log, halt = _paths(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    d = _read(root)
    values = dict(d.get("values") or {})
    hist = {k: list(v) for k, v in (d.get("acc_history") or {}).items()}
    after = dict(new) if new is not None else {**load(root), **{c.key: c.after for c in changes}}
    meta = [dict(x) for x in after.get("_extras") or d.get("extras") or []]
    today = _today_kst(now)
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).strftime("%Y-%m-%d")
    for c in changes:
        if c.key == "safety.halt":
            if c.after:
                halt.write_text(f"설정 화면에서 켬 {today}\n", encoding="utf-8")
            else:
                halt.unlink(missing_ok=True)
            continue
        if not c.key.endswith(".added"):
            values[c.key] = c.after

    def touch(sym: str, rate: float) -> None:
        rows = [r for r in hist.get(sym, []) if r[0] != tomorrow]
        hist[sym] = [*rows, [tomorrow, rate]]

    for s in ACC_SYMBOLS:
        if any(c.key.startswith(f"acc.{s}.") for c in changes):
            live = after[f"acc.{s}.on"] and not after.get(f"acc.{s}.removed")
            touch(s, float(after[f"acc.{s}.usd"]) if live else 0.0)
    for x in meta:
        k = _xkey(x)
        if not any(c.key.startswith(k + ".") for c in changes):
            continue
        if after.get(f"{k}.added"):
            x["added"] = tomorrow
            values[f"{k}.daily"], values[f"{k}.on"] = after[f"{k}.daily"], True
            values.pop(f"{k}.removed", None)
        if after.get(f"{k}.removed"):
            x["removed"] = tomorrow
            values.pop(f"{k}.removed", None)
        live = after.get(f"{k}.on") and not after.get(f"{k}.removed")
        touch(x["sym"], float(after[f"{k}.daily"]) if live else 0.0)
    d.update({"values": values, "acc_history": hist, "extras": meta})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    stamp = (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d %H:%M")
    with log.open("a", encoding="utf-8") as fh:
        for c in changes:
            what = (
                c.label
                if c.key.endswith(".added")
                else f"{c.label} {show(c.key, c.before)} → {show(c.key, c.after)}"
            )
            row = {"at": stamp, "by": by, "key": c.key, "what": what}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


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
