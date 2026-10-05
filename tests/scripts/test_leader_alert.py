from __future__ import annotations

import json
from pathlib import Path

from scripts.leader_alert import run


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_alert_lists_only_new_names_and_does_not_repeat_a_date(tmp_path: Path) -> None:
    us = tmp_path / "reports" / "lt_paper"
    _write(us / "2026-10-01.json", {"date": "2026-10-01", "leaders": [{"symbol": "AAA"}]})
    _write(
        us / "2026-10-02.json",
        {"date": "2026-10-02", "leaders": [{"symbol": "AAA"}, {"symbol": "APH"}]},
    )
    kr = tmp_path / "reports" / "kr_shadow"
    leader = {"code": "005930", "name": "삼성전자"}
    _write(
        kr / "2026-10-02.json",
        {
            "date": "2026-10-02",
            "price_leaders": [leader],
            "flow_leaders": [],
            "ai": {"leaders": [leader]},
        },
    )
    out = run(tmp_path, send=False)
    us_sec = next(s for s in out["sections"] if s["market"] == "미국")
    assert [x["code"] for x in us_sec["new"]] == ["APH"]  # AAA 는 전날에도 있었다
    assert us_sec["new"][0]["triangle"]["status"] == "자료 부족"  # 일봉 없음
    kr_sec = next(s for s in out["sections"] if s["market"] == "국장")
    assert [(x["list"], x["name"]) for x in kr_sec["new"]] == [
        ("가격", "삼성전자"),
        ("AI", "삼성전자"),
    ]
    assert "처음엔 거의 모두 새로 보임" in out["text"] and not out["sent"]
    assert (tmp_path / "reports" / "leader_alert" / "latest.json").exists()
    again = run(tmp_path, send=False)
    assert again["sections"] == [] and again["note"] == "새 기록 없음"
