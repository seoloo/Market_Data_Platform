"""사용자 설정 — 오르빗 설정 화면 · 텔레그램 '적립' 메뉴에서 바꾸는 값 (2026-10-10 시안 확정).

언제 쓰나: 실행기 · 봇 · 화면이 실행할 때마다 읽는다 (예: Deneb 월 납입, 적립 하루 금액, 알림 켜기).
읽고 쓰는 것: ``data/settings/settings.json`` (값) · ``data/settings/changes.jsonl`` (바꾼 기록) ·
              ``data/HALT`` (비상 정지 — 따로 저장하지 않고 이 파일 자체).

* 파일이 없으면 기본값 = 2026-10-10 실제 운용값이라, 설정 화면을 한 번도 안 써도 동작은 같다.
* 매매 규칙(연구로 정한 값)은 여기 없다 — 화면에서 잠금으로만 보여 준다.
* 종목 추가 · 빼기(2026-10-10): 미국은 기호, 국내는 이름 · 코드. 토스 종목 목록 파일
  (``data/toss/universe.json``, 키 없이 읽음)에서 찾은 것만 받는다. 합쳐서 최대 8 종목.
  추가 종목의 값 키는 ``x.<US|KR>.<기호>.daily|on|removed`` 다.
* 토스 적립 하루 금액 · 켜기/끄기는 **내일(KST)부터** 바뀐다. 바꾼 날짜와 금액을 기록에 남겨,
  적립금 계산(그날부터 새 금액)이 지난 날을 다시 계산하지 않게 한다. 끈 기간은 금액 0 이다.

용어: '기본 종목' = 처음부터 있던 BTQ · WQTM · QS (키 ``acc.<기호>.*``),
      '추가 종목' = 설정 화면에서 넣은 종목 (키 ``x.<US|KR>.<기호>.*``, 정보는 ``extras``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.ops.ledger_file import read_json_or_none, write_json_atomic

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
    "set_daily",
    "show",
    "validate",
]

_KST = timedelta(hours=9)


@dataclass(frozen=True, slots=True)
class Field:
    """설정 칸 하나의 정의 — 키 · 화면 이름 · 종류 · 기본값 · 언제부터 적용 · 허용 범위."""

    key: str
    """저장 키 (``deneb.monthly_krw`` 등) — 파일에 저장되는 이름이라 바꾸지 않는다."""
    label: str
    kind: str
    """'int' · 'float' · 'bool' · 'enum'."""
    default: Any
    applies: str
    """언제부터 적용되나 (확인 창에 보임)."""
    lo: float | None = None
    """숫자 칸의 최솟값 (없으면 None)."""
    hi: float | None = None
    """숫자 칸의 최댓값 (없으면 None)."""
    options: tuple[str, ...] = ()
    """'enum' 칸에서 고를 수 있는 값."""


ACC_SYMBOLS = ("BTQ", "WQTM", "QS")
"""화면에서 하루 금액 · 켜기를 바꿀 수 있는 토스 적립 종목 (QNDX 는 QS 전략과 얽혀 대화로만)."""
_ACC_DEFAULT_USD = {"BTQ": 1.0, "WQTM": 1.0, "QS": 1.0}
"""기본 종목의 처음 하루 금액 (달러)."""


def _default_acc_fields() -> list[Field]:
    """[쓰임] 기본 종목(BTQ · WQTM · QS)의 칸 — 하루 금액 3 개 · 적립 켜기 3 개 · 빼기 3 개 순."""
    amount_fields = [
        Field(
            f"acc.{symbol}.usd",
            f"{symbol} 하루 금액",
            "float",
            _ACC_DEFAULT_USD[symbol],
            "내일 적립분부터",
            0.5,
            20,
        )
        for symbol in ACC_SYMBOLS
    ]
    on_fields = [
        Field(f"acc.{symbol}.on", f"{symbol} 적립", "bool", True, "내일 적립분부터")
        for symbol in ACC_SYMBOLS
    ]
    remove_fields = [
        Field(
            f"acc.{symbol}.removed",
            f"{symbol} 목록에서 빼기",
            "bool",
            False,
            "내일 적립분부터 (보유는 그대로)",
        )
        for symbol in ACC_SYMBOLS
    ]
    return [*amount_fields, *on_fields, *remove_fields]


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
    Field(
        "deneb.gold_on",
        "금 섞기 (SCHD 절반 → 금 IAUM)",
        "bool",
        False,
        "다음 실행(매일 21:00)부터 — 새 돈 · 채우기로 맞춤",
    ),
    Field("alerts.vega_trade", "Vega 매매 알림", "bool", True, "다음 09:05 실행부터"),
    Field("alerts.vega_weekly", "Vega 주간 요약", "bool", True, "다음 토요일부터"),
    Field("alerts.fx", "환전 알림", "bool", True, "다음 환전 확인(평일 09 · 12 · 15시)부터"),
    Field("alerts.bot_down", "봇 멈춤 알림", "bool", True, "바로"),
    Field("alerts.theme", "주간 테마", "bool", True, "다음 토요일부터"),
    Field("alerts.brief", "시장 브리핑", "bool", True, "다음 브리핑부터"),
    Field("alerts.news", "종목 뉴스 답", "bool", True, "바로"),
    Field("alerts.events", "경제 일정 · 실적 경고", "bool", True, "바로"),
    Field("alerts.paper", "매일 Paper 요약 (21:30)", "bool", True, "다음 21:30 부터"),
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
    *_default_acc_fields(),
    Field("safety.halt", "비상 정지", "bool", False, "바로 (모든 새 주문)"),
)
"""고정 설정 칸 전부 (화면에 보이는 순서). 추가 종목 칸은 :func:`fields` 가 덧붙인다."""


def _settings_file(root: Path) -> Path:
    return root / "data" / "settings" / "settings.json"


def _change_log_file(root: Path) -> Path:
    return root / "data" / "settings" / "changes.jsonl"


def _halt_file(root: Path) -> Path:
    return root / "data" / "HALT"


def _read_saved(root: Path) -> dict[str, Any]:
    """[쓰임] 저장된 설정 파일 전체 ({values, acc_history, extras}). 없거나 깨졌으면 빈 dict.
    [이유] 설정 파일이 깨져도 실행기는 기본값(실제 운용값)으로 계속 돌아야 해서
           오류를 내지 않는다."""
    try:
        saved = read_json_or_none(_settings_file(root))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def extras(root: Path) -> list[dict[str, Any]]:
    """[쓰임] 추가 종목 정보 목록 ({mkt, sym, name, market, kind, daily0, added, removed}).
    [이유] 뺀 종목도 ``removed`` 날짜와 함께 남긴다 — 보유 · 적립 기록을 이어 보려고."""
    raw_items = _read_saved(root).get("extras") or []
    items = []
    for item in raw_items:
        if isinstance(item, dict) and item.get("sym") and item.get("mkt"):
            items.append(dict(item))
    return items


def _extra_key(item: Mapping[str, Any]) -> str:
    """[쓰임] 추가 종목의 설정 키 머리 (``x.KR.005930`` · ``x.US.TSLA``)."""
    return f"x.{item['mkt']}.{item['sym']}"


def _extra_fields(meta: Sequence[Mapping[str, Any]]) -> list[Field]:
    """[쓰임] 추가 종목마다 칸 3 개 (하루 금액 · 적립 켜기 · 목록에서 빼기). 뺀 종목은 칸 없음.

    국내는 원(₩500 ~ 30,000, 기본 5,000), 미국은 달러($0.5 ~ 20, 기본 $1)."""
    result = []
    for item in meta:
        if item.get("removed"):
            continue
        key = _extra_key(item)
        name = f"{item.get('name') or item['sym']} ({item['sym']})"
        korean = item["mkt"] == "KR"
        result += [
            Field(
                f"{key}.daily",
                f"{name} 하루 금액",
                "int" if korean else "float",
                float(item.get("daily0") or (5_000 if korean else 1.0)),
                "내일 적립분부터",
                500 if korean else 0.5,
                30_000 if korean else 20,
            ),
            Field(f"{key}.on", f"{name} 적립", "bool", True, "내일 적립분부터"),
            Field(
                f"{key}.removed",
                f"{name} 목록에서 빼기",
                "bool",
                False,
                "내일 적립분부터 (보유는 그대로)",
            ),
        ]
    return result


def fields(meta: Sequence[Mapping[str, Any]]) -> list[Field]:
    """[쓰임] 지금 있는 설정 칸 전부 — 고정 칸(:data:`FIELDS`) + 추가 종목 칸."""
    return [*FIELDS, *_extra_fields(meta)]


def _find_field(meta: Sequence[Mapping[str, Any]], key: str) -> Field | None:
    for setting in fields(meta):
        if setting.key == key:
            return setting
    return None


_OLD_KEYS = {
    "deneb.monthly_krw": "glide.monthly_krw",
    "deneb.max_krw": "glide.max_krw",
    "deneb.ask_on": "glide.ask_on",
    "deneb.harvest_on": "glide.harvest_on",
    "alerts.vega_trade": "alerts.cruise_trade",
    "alerts.vega_weekly": "alerts.cruise_weekly",
}
"""지금 키 → 2026-10-15 이름 변경 전 키 — 저장된 옛 값이 있으면 그대로 쓴다."""


def load(root: Path) -> dict[str, Any]:
    """[쓰임] 설정 값 전부 {키: 값} — 저장 안 된 칸은 기본값, ``safety.halt`` 는 HALT 파일이 있는지.
    [이유] ``_extras`` 에 추가 종목 정보(이름 · 시장)를 함께 싣는다 —
           화면 · 검사 · 기록이 같이 쓴다."""
    saved = _read_saved(root).get("values") or {}
    meta = extras(root)
    values: dict[str, Any] = {}
    for setting in fields(meta):
        if setting.key in saved:
            values[setting.key] = saved[setting.key]
            continue
        old_key = _OLD_KEYS.get(setting.key)
        if old_key is not None:
            values[setting.key] = saved.get(old_key, setting.default)
        else:
            values[setting.key] = setting.default
    values["safety.halt"] = _halt_file(root).exists()
    values["_extras"] = meta
    return values


def get(root: Path, key: str) -> Any:
    """[쓰임] 설정 하나만 읽는다 (예: ``get(REPO, "deneb.gold_on")``)."""
    return load(root)[key]


def acc_history(root: Path, symbol: str) -> list[tuple[str, float]]:
    """[쓰임] 그 종목의 [(그날부터, 하루 금액)] 기록 — 끈 · 뺀 기간은 0.
    [이유] 적립금 계산(``Plan.changes`` 뒤에 붙임)이 지난 날을 옛 금액 그대로 계산하게."""
    raw_rows = (_read_saved(root).get("acc_history") or {}).get(symbol) or []
    return [(str(day), float(rate)) for day, rate in raw_rows]


def _parse_value(setting: Field, raw: str | None) -> Any:
    """[쓰임] 화면 칸에 적힌 글자 → 값. 범위를 벗어나면 ValueError (화면에 그대로 보일 문구).
    [이유] 사람이 '30,000원' · '$1.5' 처럼 적어도 받도록 쉼표 · 원 · $ · ₩ 를 뗀다."""
    if setting.kind == "bool":
        return raw in ("on", "1", "true")
    text = (raw or "").replace(",", "").replace("원", "").replace("$", "")
    text = text.replace("₩", "").replace("\\", "").strip()
    if setting.kind == "enum":
        if text not in setting.options:
            raise ValueError("고를 수 없는 값")
        return text
    try:
        number = float(text)
    except ValueError:
        raise ValueError("숫자로 적어 주세요") from None
    too_low = setting.lo is not None and number < setting.lo
    too_high = setting.hi is not None and number > setting.hi
    if too_low or too_high:
        if setting.key.startswith(("acc.", "x.US.")):
            raise ValueError(f"${setting.lo:,g} ~ ${setting.hi:,g} 사이로 적어 주세요")
        if setting.key.startswith("x.KR."):
            raise ValueError(f"₩{setting.lo:,g} ~ ₩{setting.hi:,g} 사이로 적어 주세요")
        raise ValueError(f"{setting.lo:,g} ~ {setting.hi:,g} 사이로 적어 주세요")
    if setting.kind == "int":
        return round(number)
    return round(number, 2)


def active_count(values: Mapping[str, Any]) -> int:
    """[쓰임] 목록에 있는 적립 종목 수 (뺀 것 빼고) — 최대 :data:`MAX_ITEMS` 검사용."""
    count = 0
    for symbol in ACC_SYMBOLS:
        if not values.get(f"acc.{symbol}.removed"):
            count += 1
    for item in values.get("_extras") or []:
        if not item.get("removed") and not values.get(f"{_extra_key(item)}.removed"):
            count += 1
    return count


def validate(
    form: Mapping[str, str],
    current: Mapping[str, Any],
    universe: Sequence[Any] = (),
) -> tuple[dict[str, Any], dict[str, str]]:
    """[쓰임] 설정 화면 폼 → (새 값, 키별 오류).
    [이유] 체크 안 한 체크칸은 폼에 아예 안 실려 와서, 폼에 없는 bool 은 '꺼짐' 으로 본다.
           그 밖에 폼에 없는 칸은 그대로 둔다.

    ``new.query`` 가 있으면 ``universe`` (ops.kr_accumulate.Listed 목록)에서 찾아 추가한다."""
    new = dict(current)
    meta = [dict(item) for item in current.get("_extras") or []]
    new["_extras"] = meta
    errors: dict[str, str] = {}
    # 1 단계: 칸마다 글자 → 값
    for setting in fields(meta):
        if setting.key == "safety.halt":
            continue  # 비상 정지는 전용 버튼 · 확인 창으로만 (큰 폼 저장이 끄지 못하게)
        if setting.kind == "bool":
            new[setting.key] = _parse_value(setting, form.get(setting.key))
            continue
        if setting.key not in form:
            continue
        try:
            new[setting.key] = _parse_value(setting, form[setting.key])
        except ValueError as exc:
            errors[setting.key] = str(exc)
    # 2 단계: 종목 추가 칸
    query = (form.get("new.query") or "").strip()
    if query:
        _add_extra_item(form, query, new, meta, universe, errors)
    # 3 단계: 칸끼리 맞는지
    if not errors:
        if not new["fire.rate_good"] >= new["fire.rate_mid"] >= new["fire.rate_bad"]:
            errors["fire.rate_mid"] = "좋을 때 ≥ 보통 ≥ 나쁠 때 순서로"
        if new["deneb.monthly_krw"] > new["deneb.max_krw"]:
            errors["deneb.monthly_krw"] = "한 달 상한보다 클 수 없어요"
    return new, errors


def _add_extra_item(
    form: Mapping[str, str],
    query: str,
    new: dict[str, Any],
    meta: list[dict[str, Any]],
    universe: Sequence[Any],
    errors: dict[str, str],
) -> None:
    """[쓰임] 토스 종목 목록에서 ``query`` 를 찾아 추가 종목으로 넣는다 (``new`` · ``meta`` 고침).
    [이유] 정확히 한 종목만 맞을 때만 넣는다 — 잘못 고른 종목에 매일 돈이 들어가지 않게.
           문제가 있으면 ``errors["new.query"]`` (또는 ``new.daily``) 에 읽을 문구를 남긴다."""
    from auto_trading.ops.kr_accumulate import find

    market = "KR" if form.get("new.mkt") == "KR" else "US"
    if not universe:
        errors["new.query"] = "토스 종목 목록을 아직 못 받았어요 — 잠시 뒤 다시"
        return
    # 1 단계: 찾기 — 하나만 맞아야 한다
    hits = find(query, universe, market)
    if not hits:
        errors["new.query"] = "찾지 못했어요 — 기호(미국) · 이름 또는 6 자리 코드(국내)로"
        return
    if len(hits) > 1:
        names = " · ".join(f"{hit.name}({hit.symbol})" for hit in hits)
        errors["new.query"] = "여러 개가 맞아요: " + names
        return
    hit = hits[0]
    # 2 단계: 넣을 수 없는 경우
    if hit.symbol == "QNDX":
        errors["new.query"] = "QNDX 는 QS 전략과 얽혀 대화로만 바꿔요"
        return
    if market == "US" and hit.symbol in ACC_SYMBOLS:
        if new.get(f"acc.{hit.symbol}.removed"):
            new[f"acc.{hit.symbol}.removed"] = False  # 뺐던 기본 종목을 다시 넣기
            return
        errors["new.query"] = f"{hit.symbol} 는 이미 목록에 있어요"
        return
    for item in meta:
        if item["sym"] == hit.symbol and not item.get("removed"):
            errors["new.query"] = f"{hit.name}({hit.symbol}) 는 이미 목록에 있어요"
            return
    if active_count(new) >= MAX_ITEMS:
        errors["new.query"] = f"종목은 합쳐서 {MAX_ITEMS} 개까지예요 — 하나를 빼고 추가해 주세요"
        return
    # 3 단계: 하루 금액 검사
    new_item: dict[str, Any] = {
        "mkt": market,
        "sym": hit.symbol,
        "name": hit.name,
        "market": hit.market,
        "kind": hit.kind,
    }
    daily_field = _extra_fields([new_item])[0]
    try:
        daily = _parse_value(daily_field, form.get("new.daily") or str(daily_field.default))
    except ValueError as exc:
        errors["new.daily"] = str(exc)
        return
    # 4 단계: 넣기 (뺐던 종목을 다시 넣으면 옛 정보는 지우고 새로)
    new_item["daily0"] = daily
    meta[:] = [item for item in meta if item["sym"] != hit.symbol]
    meta.append(new_item)
    key = _extra_key(new_item)
    new[f"{key}.daily"] = daily
    new[f"{key}.on"] = True
    new[f"{key}.removed"] = False
    new[f"{key}.added"] = True


def acc_items(values: Mapping[str, Any]) -> list[dict[str, Any]]:
    """[쓰임] 지금 적립 목록(뺀 것 빼고) — 텔레그램 '적립' 메뉴 · 정리 (2026-10-18).

    한 줄 = {key: 설정 키 머리, sym, name, mkt: US · KR, daily: 하루 금액(달러 · 원), on}."""
    items: list[dict[str, Any]] = []
    for symbol in ACC_SYMBOLS:
        if values.get(f"acc.{symbol}.removed"):
            continue
        items.append(
            {
                "key": f"acc.{symbol}",
                "sym": symbol,
                "name": symbol,
                "mkt": "US",
                "daily": float(values.get(f"acc.{symbol}.usd", _ACC_DEFAULT_USD[symbol])),
                "on": bool(values.get(f"acc.{symbol}.on", True)),
            }
        )
    for item in values.get("_extras") or []:
        key = _extra_key(item)
        if item.get("removed") or values.get(f"{key}.removed"):
            continue
        items.append(
            {
                "key": key,
                "sym": str(item["sym"]),
                "name": str(item.get("name") or item["sym"]),
                "mkt": str(item["mkt"]),
                "daily": float(values.get(f"{key}.daily", item.get("daily0") or 0)),
                "on": bool(values.get(f"{key}.on", True)),
            }
        )
    return items


def add_item(
    current: Mapping[str, Any], query: str, mkt: str, daily: str, universe: Sequence[Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """[쓰임] 텔레그램 '적립' 메뉴의 종목 추가 → (새 값, 오류). ``daily`` 빈 값이면 기본.
    [이유] 설정 화면 '추가' 와 같은 검사를 쓰려고 같은 함수(:func:`_add_extra_item`)를 부른다."""
    new = dict(current)
    meta = [dict(item) for item in current.get("_extras") or []]
    new["_extras"] = meta
    errors: dict[str, str] = {}
    form = {"new.mkt": "KR" if mkt == "KR" else "US", "new.daily": daily}
    _add_extra_item(form, query, new, meta, universe, errors)
    return new, errors


def set_daily(
    current: Mapping[str, Any], key: str, amount: str
) -> tuple[dict[str, Any], dict[str, str]]:
    """[쓰임] 적립 종목(``key`` = acc.BTQ · x.KR.005930 …)의 하루 금액 → (새 값, 오류).

    설정 화면의 그 종목 금액 칸과 같은 검사(미국 $0.5 ~ 20 · 국내 ₩500 ~ 30,000)."""
    value_key = f"{key}.usd" if key.startswith("acc.") else f"{key}.daily"
    setting = _find_field(current.get("_extras") or [], value_key)
    if setting is None or current.get(f"{key}.removed"):
        return dict(current), {"amount": f"적립 목록에 없는 종목: {key}"}
    try:
        value = _parse_value(setting, amount)
    except ValueError as exc:
        return dict(current), {"amount": str(exc)}
    return {**current, value_key: value}, {}


def remove_item(current: Mapping[str, Any], key: str) -> dict[str, Any]:
    """[쓰임] 설정 화면 '삭제' 와 같음 — ``{key}.removed`` 를 켠다.

    내일 적립분부터 빠지고, 보유는 그대로다."""
    flag = f"{key}.removed"
    if flag not in current or current[flag]:
        raise ValueError(f"적립 목록에 없는 종목: {key}")
    return {**current, flag: True}


@dataclass(frozen=True, slots=True)
class Change:
    """바뀐 칸 하나 (확인 창 · 바꾼 기록에 한 줄)."""

    key: str
    label: str
    before: Any
    after: Any
    applies: str


def show(key: str, value: Any) -> str:
    """[쓰임] 설정 값을 사람이 읽는 글자로 (켜짐 · 꺼짐 · $1 · ₩5,000 · 연 12% · 300,000원)."""
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
    """[쓰임] 지금 값 → 새 값에서 바뀐 칸 목록 (확인 창에 보일 것).
    [이유] 새로 넣은 종목은 칸 3 개를 따로 보이지 않고 '추가 · 하루 $1' 한 줄로 묶는다."""
    changes = []
    meta = new.get("_extras") or []
    for item in meta:
        key = _extra_key(item)
        if not new.get(f"{key}.added"):
            continue
        amount = new[key + ".daily"]
        amount_text = f"₩{amount:,.0f}" if item["mkt"] == "KR" else f"${amount:g}"
        where = "국내" if item["mkt"] == "KR" else "미국"
        changes.append(
            Change(
                f"{key}.added",
                f"{where} {item['name']}({item['sym']}) 추가 · 하루 {amount_text}",
                None,
                True,
                "내일 적립분부터",
            )
        )
    for setting in fields(meta):
        item_key = setting.key.rsplit(".", 1)[0]
        if setting.key.startswith("x.") and new.get(item_key + ".added"):
            continue  # 새로 넣은 종목은 위의 '추가' 한 줄로
        before = current.get(setting.key, setting.default)
        after = new.get(setting.key, setting.default)
        if before != after:
            changes.append(
                Change(setting.key, setting.label, before, new[setting.key], setting.applies)
            )
    return changes


def _today_kst(now: datetime) -> str:
    return (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d")


def apply(
    root: Path,
    changes: Sequence[Change],
    now: datetime,
    by: str = "설정 화면",
    new: Mapping[str, Any] | None = None,
) -> None:
    """[쓰임] 바뀐 칸을 저장한다 — 설정 파일 · 바꾼 기록 · 비상 정지 파일 · 적립 금액 기록.
    [이유] 적립 금액은 '내일부터' 바뀌어야 지난 날 적립금이 다시 계산되지 않는다 —
           그래서 금액 기록(``acc_history``)에 [내일, 새 금액] 줄을 남긴다.
           바뀐 것이 없으면 아무것도 안 한다.

    ``new`` 는 검사를 통과한 새 값 전체(추가 종목 정보 ``_extras`` 포함)."""
    if not changes:
        return
    settings_file, change_log, halt = (
        _settings_file(root),
        _change_log_file(root),
        _halt_file(root),
    )
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    saved = _read_saved(root)
    values = dict(saved.get("values") or {})
    history = {symbol: list(rows) for symbol, rows in (saved.get("acc_history") or {}).items()}
    if new is not None:
        after = dict(new)
    else:
        after = {**load(root), **{change.key: change.after for change in changes}}
    meta = [dict(item) for item in after.get("_extras") or saved.get("extras") or []]
    today = _today_kst(now)
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).strftime("%Y-%m-%d")

    # 1 단계: 값 저장 (비상 정지는 파일로)
    for change in changes:
        if change.key == "safety.halt":
            if change.after:
                halt.write_text(f"설정 화면에서 켬 {today}\n", encoding="utf-8")
            else:
                halt.unlink(missing_ok=True)
            continue
        if not change.key.endswith(".added"):
            values[change.key] = change.after

    def record_rate_from_tomorrow(symbol: str, rate: float) -> None:
        """그 종목의 금액 기록에 [내일, 금액] 한 줄 (내일 줄이 이미 있으면 바꿔 쓴다)."""
        rows = [row for row in history.get(symbol, []) if row[0] != tomorrow]
        history[symbol] = [*rows, [tomorrow, rate]]

    # 2 단계: 기본 종목 금액 기록 (끄거나 뺐으면 0)
    for symbol in ACC_SYMBOLS:
        if any(change.key.startswith(f"acc.{symbol}.") for change in changes):
            live = after[f"acc.{symbol}.on"] and not after.get(f"acc.{symbol}.removed")
            record_rate_from_tomorrow(symbol, float(after[f"acc.{symbol}.usd"]) if live else 0.0)
    # 3 단계: 추가 종목 — 추가 · 뺀 날짜 · 금액 기록
    for item in meta:
        key = _extra_key(item)
        if not any(change.key.startswith(key + ".") for change in changes):
            continue
        if after.get(f"{key}.added"):
            item["added"] = tomorrow
            values[f"{key}.daily"] = after[f"{key}.daily"]
            values[f"{key}.on"] = True
            values.pop(f"{key}.removed", None)
        if after.get(f"{key}.removed"):
            item["removed"] = tomorrow
            values.pop(f"{key}.removed", None)
        live = after.get(f"{key}.on") and not after.get(f"{key}.removed")
        record_rate_from_tomorrow(item["sym"], float(after[f"{key}.daily"]) if live else 0.0)
    # 4 단계: 파일에 쓰기 · 바꾼 기록 한 줄씩
    saved.update({"values": values, "acc_history": history, "extras": meta})
    write_json_atomic(settings_file, saved)
    stamp = (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d %H:%M")
    with change_log.open("a", encoding="utf-8") as log:
        for change in changes:
            if change.key.endswith(".added"):
                what = change.label
            else:
                before_text = show(change.key, change.before)
                after_text = show(change.key, change.after)
                what = f"{change.label} {before_text} → {after_text}"
            row = {"at": stamp, "by": by, "key": change.key, "what": what}
            log.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_log(root: Path, limit: int = 30) -> list[dict[str, str]]:
    """[쓰임] 바꾼 기록 최근 ``limit`` 줄 (최신 먼저) — 설정 화면 '바꾼 기록'.
    [이유] 깨진 줄은 건너뛴다 (기록 한 줄이 망가져도 화면이 열리게)."""
    try:
        lines = _change_log_file(root).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in reversed(lines[-limit * 3 :]):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append({key: str(value) for key, value in row.items()})
    return rows[:limit]
