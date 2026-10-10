"""봇 서로 지켜보기: 상대가 15 분 조용하면 한 번 알리고, 돌아오면 한 번. 깬 직후엔 판단 안 함."""

from __future__ import annotations

from pathlib import Path

from auto_trading.notify import heartbeat as hb

M = 60_000


def test_decide() -> None:
    now = 1_000 * M
    assert hb.decide(now, None, None, False) is None  # 첫 실행
    assert hb.decide(now, now - 30 * M, now - 60 * M, False) is None  # 나도 막 깸
    assert hb.decide(now, now - 2 * M, now - 16 * M, False) == "down"
    assert hb.decide(now, now - 2 * M, now - 16 * M, True) is None  # 이미 알림
    assert hb.decide(now, now - 2 * M, now - 1 * M, True) == "up"
    assert hb.decide(now, now - 2 * M, now - 1 * M, False) is None


def test_beat_alerts_once_then_recovers(tmp_path: Path) -> None:
    t = 1_000 * M
    assert hb.beat(tmp_path, "info_bot", t) is None
    assert hb.beat(tmp_path, "telegram_bot", t) is None
    # 현황 봇이 멈추고 정보 봇만 1 분마다 돈다
    texts = [hb.beat(tmp_path, "info_bot", t + k * M) for k in range(1, 20)]
    downs = [x for x in texts if x]
    assert len(downs) == 1 and downs[0].startswith("⚠ 현황 봇 멈춤")
    assert "AutoTrading_Telegram_Bot" in downs[0]
    assert hb.beat(tmp_path, "telegram_bot", t + 20 * M) is None  # 깬 직후라 판단 안 함
    assert hb.beat(tmp_path, "info_bot", t + 21 * M) == "✅ 현황 봇 다시 정상"
    assert not (tmp_path / "data" / "telegram" / "heartbeat.lock").exists()


def test_daily_findings_only_after_an_hour(tmp_path: Path) -> None:
    assert hb.daily_findings(tmp_path, 0) == []  # 기록 없음 = 아직 안 켬
    hb.write(tmp_path, {"telegram_bot": 0, "info_bot": 50 * M})
    found = hb.daily_findings(tmp_path, 70 * M)
    assert [label for label, _ in found] == ["현황 봇"]
    assert "70분째" in found[0][1]
