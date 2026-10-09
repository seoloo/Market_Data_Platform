"""주도주 '처음 잡힘' 알림 (IDEAS 7) — 저장된 보고서만 읽는다. 주문·계좌 조회 없음.

사용 예::

    python -m scripts.leader_alert             # 새 기록이 있으면 알림 (텔레그램 + 로컬 기록)
    python -m scripts.leader_alert --no-send   # 텔레그램 없이 화면·로컬 기록만
    python -m scripts.leader_alert --force     # 이미 알린 날짜도 다시 계산

* 미국: ``reports/lt_paper/<날짜>.json`` 의 주도주(문서 115) ·
  일봉 ``data/lt_paper/bars``(고가·저가·종가).
* 국장: ``reports/kr_shadow/<날짜>.json`` 의 가격·수급·AI 주도주(문서 143) ·
  일봉 ``data/kr_shadow/prices``
  (종가만 있어 삼각형은 종가 기준).
* 새로 들어온 종목 = 최근 20 번의 기록에 한 번도 없던 종목. 같은 날짜는 한 번만 알린다(상태 파일).
* 삼각형 판정은 참고 규칙이다 — 국장 검증(문서 146)에서 돌파 진입이 무작위 시점보다 나빴다
  (:data:`auto_trading.ops.leader_alert.TRIANGLE_RULE`).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.notify.style import RULE, sub
from auto_trading.ops.leader_alert import TRIANGLE_RULE, Triangle, ascending_triangle, new_entries

REPO = Path(__file__).resolve().parent.parent
LOOKBACK = 20
FEW = 3
"""비교할 과거 기록이 이보다 적으면 '거의 모두 새로 보임' 이라고 적는다."""


def _reports(folder: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted(folder.glob("20*.json")):
        try:
            rep = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rep, dict) and rep.get("date"):
            out.append(rep)
    return sorted(out, key=lambda r: str(r["date"]))


def _us_bars(root: Path, sym: str) -> tuple[list[float], list[float], list[float]]:
    path = root / "data" / "lt_paper" / "bars" / f"{sym}.csv"
    if not path.exists():
        return [], [], []
    hs, ls, cs = [], [], []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split(",")
        try:
            hs.append(float(cells[2]))
            ls.append(float(cells[3]))
            cs.append(float(cells[4]))
        except (IndexError, ValueError):
            continue
    return hs, ls, cs


def _kr_closes(root: Path, code: str) -> list[float]:
    path = root / "data" / "kr_shadow" / "prices" / f"{code}.csv"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        cells = line.split(",")
        try:
            if float(cells[2]) > 0:  # 거래량 0(정지) 날은 빼고
                out.append(float(cells[1]))
        except (IndexError, ValueError):
            continue
    return out


def _tri(t: Triangle) -> dict[str, Any]:
    return asdict(t)


def us_section(root: Path) -> dict[str, Any] | None:
    reps = _reports(root / "reports" / "lt_paper")
    if not reps:
        return None
    lists = [[str(x.get("symbol")) for x in r.get("leaders") or []] for r in reps]
    fresh = new_entries(lists[-1], lists[:-1], LOOKBACK)
    items = []
    for sym in fresh:
        hs, ls, cs = _us_bars(root, sym)
        items.append(
            {
                "code": sym,
                "name": sym,
                "list": "주도주",
                "triangle": _tri(ascending_triangle(hs, ls, cs)),
            }
        )
    return {"market": "미국", "date": reps[-1]["date"], "history": len(lists) - 1, "new": items}


def kr_section(root: Path) -> dict[str, Any] | None:
    reps = _reports(root / "reports" / "kr_shadow")
    if not reps:
        return None
    items: list[dict[str, Any]] = []
    for key, label in (("price_leaders", "가격"), ("flow_leaders", "수급"), ("ai", "AI")):

        def codes(r: dict[str, Any], key: str = key) -> list[str]:
            rows = (r.get("ai") or {}).get("leaders") if key == "ai" else r.get(key)
            return [str(x.get("code")) for x in rows or []]

        lists = [codes(r) for r in reps]
        names = {
            str(x.get("code")): str(x.get("name"))
            for x in (
                (reps[-1].get("ai") or {}).get("leaders") if key == "ai" else reps[-1].get(key)
            )
            or []
        }
        for code in new_entries(lists[-1], lists[:-1], LOOKBACK):
            cs = _kr_closes(root, code)
            items.append(
                {
                    "code": code,
                    "name": names.get(code, code),
                    "list": label,
                    "triangle": _tri(ascending_triangle(cs, cs, cs)),
                }
            )
    return {"market": "국장", "date": reps[-1]["date"], "history": len(reps) - 1, "new": items}


LIST_NAME = {"주도주": "가격 주도주", "가격": "가격 주도주", "AI": "AI 테마 선정"}
TRI_ICON = {"돌파": "🚀", "형성 중": "📐", "아님": "▫️"}


def render(sections: Sequence[dict[str, Any]]) -> str:
    """2026-10-09 사용자 "주도주 알림도 좀 꾸며주면" — 종목마다 목록 · 삼각형 아이콘 한 줄."""
    lines = ["🌟 [주도주 새로 잡힘]", sub("최근 20 번 기록에 없던 종목 · 정보용, 매매 아님")]
    flag = {"미국": "🇺🇸", "국장": "🇰🇷"}
    for sec in sections:
        day = str(sec["date"])[5:].replace("-", "/")
        lines += [
            "",
            RULE,
            f"{flag.get(sec['market'], '■')} {sec['market']} {day} 종가"
            f" · 새 종목 {len(sec['new'])}",
        ]
        if sec["history"] < FEW:
            lines.append(sub(f"비교할 과거 기록 {sec['history']} 번 — 처음엔 거의 모두 새로 보임"))
        for it in sec["new"]:
            t = it["triangle"]
            label = f"{it['name']}" + (f" ({it['code']})" if it["name"] != it["code"] else "")
            lines.append(f"🆕 {label} · {LIST_NAME.get(it['list'], it['list'])}")
            gap = t.get("gap_to_resistance")
            where = f" · 저항선까지 {gap:+.1%}" if gap is not None else ""
            lines.append(sub(f"{TRI_ICON.get(t['status'], '❔')} 삼각형 {t['status']}{where}"))
    lines += [
        "",
        RULE,
        "📐 삼각형 = 60 거래일 최고가(저항선)를 2 번 이상 두드리며 저점이 높아지는 모양",
        sub("🚀 돌파 = 오늘 종가가 저항선 위 · 📐 형성 중 · ▫️ 아님"),
        "⚠️ 돌파 진입은 국장 검증(문서 146)에서 무작위 시점보다 나빴다(가짜 돌파 66%) — 참고만",
    ]
    if sections and any(s["market"] == "국장" for s in sections):
        lines.append(sub("국장은 종가 기준으로 판정"))
    return "\n".join(lines)


def run(root: Path = REPO, *, send: bool = True, force: bool = False) -> dict[str, Any]:
    from auto_trading.config.secrets import load_telegram_credentials
    from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier

    state_path = root / "data" / "leader_alert" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    sections = []
    for sec in (us_section(root), kr_section(root)):
        if sec is None:
            continue
        if not force and state.get(sec["market"]) == sec["date"]:
            continue  # 이미 알린 날짜
        sections.append(sec)
    out_dir = root / "reports" / "leader_alert"
    result: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sections": sections,
        "rule": TRIANGLE_RULE,
        "sent": False,
    }
    if not sections:
        result["note"] = "새 기록 없음"
        return result
    text = render(sections)
    out_dir.mkdir(parents=True, exist_ok=True)
    OutboxNotifier(out_dir / "outbox.log").send(text)
    has_new = any(sec["new"] for sec in sections)
    creds = load_telegram_credentials(root / ".env")
    if send and has_new and creds is not None:
        telegram = TelegramNotifier(creds)
        try:
            telegram.send(text)
            result["sent"] = True
        except NotifyError as exc:
            OutboxNotifier(out_dir / "outbox.log").send(f"텔레그램 전송 실패 — 로컬 기록만: {exc}")
        finally:
            telegram.close()
    stamp = max(str(sec["date"]) for sec in sections)
    for path in (out_dir / f"{stamp}.json", out_dir / "latest.json"):
        path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for sec in sections:
        state[sec["market"]] = sec["date"]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    result["text"] = text
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-send", action="store_true", help="텔레그램을 보내지 않는다")
    parser.add_argument("--force", action="store_true", help="이미 알린 날짜도 다시 계산")
    args = parser.parse_args(argv)
    out = run(send=not args.no_send, force=args.force)
    sys.stdout.write(str(out.get("text") or out.get("note")) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
