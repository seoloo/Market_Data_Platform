"""정보 봇 답의 **성적표** — AI 의견 · 추세 상태를 기록해 두고 20 거래일 뒤 채점한다.

사용자 2026-10-11 요청.

AI 가 말하는 '확률' 은 검증되지 않은 숫자라 보여 주지 않는다. 대신 답할 때마다 그때의 의견을 남기고,
나중에 실제로 맞은 비율을 센다 — 표본이 쌓이면 그것이 진짜 '맞을 확률' 에 가장 가깝다.

* 기록: ``data/info_bot/calls.jsonl`` 한 줄 = 한 답 (같은 날 같은 종목은 처음 것만).
* 채점: 기록일 종가 → 20 거래일 뒤 종가. 강세면 오르면 맞음, 약세면 내리면 맞음, 중립은 세지 않는다.
* Laya 그림자 비교: 기사마다 Laya 의 호재/악재와 Gemini 의 tone 을 같이 남겨 일치율만 센다.
  어느 쪽이 '맞는지' 의 정답은 없다 — 붙일지는 사용자가 정한다.

순수 함수 + 작은 파일 읽기/쓰기만.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Call", "append_call", "laya_text", "load_calls", "record_line", "score", "score_text"]

HORIZON = 20


@dataclass
class Call:
    date: str
    """기록에 쓴 마지막 종가의 날짜 (YYYY-MM-DD)."""
    query: str
    yahoo: str
    close: float
    trend_on: bool | None
    view: str
    """강세 · 중립 · 약세 · '' (의견 없음)."""
    outcome: float | None = None
    """20 거래일 뒤 수익 (채점 전 None)."""
    tones: dict[str, str] = field(default_factory=dict)
    """기사 번호 → Gemini tone (호재 · 악재 · 중립)."""
    laya: dict[str, str] = field(default_factory=dict)
    """기사 번호 → Laya 판단 (호재 · 악재 · 중립). 그림자 기록."""
    laya_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_calls(path: Path) -> list[Call]:
    out: list[Call] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
            out.append(Call(**d))
        except (ValueError, TypeError):
            continue
    return out


def append_call(path: Path, call: Call) -> bool:
    """같은 날 같은 종목이 이미 있으면 남기지 않는다 (False)."""
    if any(c.date == call.date and c.yahoo == call.yahoo for c in load_calls(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(call.to_dict(), ensure_ascii=False) + "\n")
    return True


def save_calls(path: Path, calls: Sequence[Call]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "".join(json.dumps(c.to_dict(), ensure_ascii=False) + "\n" for c in calls), encoding="utf-8"
    )
    tmp.replace(path)


def score(
    calls: Sequence[Call], series: Mapping[str, Sequence[tuple[str, float]]], horizon: int = HORIZON
) -> int:
    """채점 안 된 기록에 결과를 채운다 (기록일 종가 → ``horizon`` 거래일 뒤 종가). 채운 수."""
    done = 0
    for c in calls:
        if c.outcome is not None:
            continue
        rows = series.get(c.yahoo)
        if not rows:
            continue
        days = [d for d, _ in rows]
        if c.date not in days:
            continue
        k = days.index(c.date)
        if k + horizon < len(rows):
            c.outcome = rows[k + horizon][1] / rows[k][1] - 1
            done += 1
    return done


def _hit(c: Call) -> bool | None:
    if c.outcome is None or c.view not in ("강세", "약세"):
        return None
    return c.outcome > 0 if c.view == "강세" else c.outcome < 0


def record_line(calls: Sequence[Call]) -> str:
    """답 끝에 붙일 한 줄."""
    hits = [h for c in calls if (h := _hit(c)) is not None]
    wait = sum(1 for c in calls if c.outcome is None)
    if len(hits) < 5:
        return f"🧾 AI 의견 성적: 채점 {len(hits)}건 (5건부터 보여요) · 채점 대기 {wait}건"
    return (
        f"🧾 AI 의견 성적: 채점 {len(hits)}건 중 맞음 {sum(hits) / len(hits):.0%} · 대기 {wait}건"
    )


def score_text(calls: Sequence[Call]) -> str:
    """'성적' 명령 답."""
    lines = ["🧾 정보 봇 성적표 (20거래일 뒤 채점)"]
    for view in ("강세", "약세"):
        xs = [c for c in calls if c.view == view and c.outcome is not None]
        if xs:
            hit = sum(1 for c in xs if _hit(c)) / len(xs)
            avg = sum(c.outcome or 0.0 for c in xs) / len(xs)
            lines.append(f"AI {view}: {len(xs)}건 · 맞음 {hit:.0%} · 평균 {avg:+.1%}")
        else:
            lines.append(f"AI {view}: 채점된 것 없음")
    for on in (True, False):
        xs = [c for c in calls if c.trend_on is on and c.outcome is not None]
        if xs:
            up = sum(1 for c in xs if (c.outcome or 0) > 0) / len(xs)
            name = "켜짐" if on else "꺼짐"
            lines.append(f"추세 {name}: {len(xs)}건 · 오른 비율 {up:.0%}")
    wait = sum(1 for c in calls if c.outcome is None)
    lines += [
        f"채점 대기 {wait}건 · 전체 기록 {len(calls)}건",
        "※ 같은 시기에 몰린 기록은 서로 독립이 아니라 건수보다 증거가 약해요",
    ]
    return "\n".join(lines)


def laya_text(calls: Sequence[Call]) -> str:
    """Laya 그림자 비교 — 기사별 판단이 Gemini 와 같은 비율 · 평균 응답 시간."""
    pairs = [(c.tones[k], v) for c in calls for k, v in c.laya.items() if c.tones.get(k)]
    times = [c.laya_ms for c in calls if c.laya]
    if not pairs:
        return "🧪 Laya 비교: 아직 기록 없음 (Laya 주소 · 키 확인 필요)"
    same = sum(1 for g, lay in pairs if g == lay) / len(pairs)
    ms = sum(times) / len(times) if times else 0.0
    return (
        f"🧪 Laya 비교 (그림자): 기사 {len(pairs)}건 · Gemini 와 같은 판단 {same:.0%} · "
        f"평균 {ms:.0f}ms\n※ 정답이 없는 비교예요 — 붙일지는 직접 정해 주세요"
    )
