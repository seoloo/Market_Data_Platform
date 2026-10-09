from __future__ import annotations

from auto_trading.ops.m3_guard import (
    Brake,
    Proposal,
    add_deposit,
    answer,
    brake_lines,
    brake_sell,
    div_step_message,
    drawdown,
    expire,
    harvest_deadline,
    harvest_message,
    lev_cap,
    monthly_dividend_krw,
    parse_callback,
    parse_reply,
    press,
    proposal_buttons,
    rebought,
    rebuy_due,
    record_close,
    remind_due,
    shift_plan,
    sold,
    step_due,
    typed,
)


def test_brake_h_uses_higher_of_unit_cost_and_12m_high_then_two_steps() -> None:
    b = Brake(ref="H")
    add_deposit(b, 1000.0, 0.0)  # NAV 1 로 1,000 단위
    record_close(b, "2026-11-02", 1200.0, 100.0)  # NAV 1.2
    record_close(b, "2026-11-03", 1000.0, 80.0)  # NAV 1.0 → 고점 1.2 대비 −16.7%
    assert round(drawdown(b, 80.0) or 0, 3) == 0.167
    assert brake_sell(b, drawdown(b, 80.0), 7) == 3 and b.stage == 1  # 절반(내림)
    sold(b, "2026-11-03", 0.167, 3, 238.5)
    assert brake_sell(b, 0.2, 4) == 0  # 단계 1 에서 −30% 전에는 더 안 판다
    assert brake_sell(b, 0.31, 4) == 4 and b.stage == 2
    sold(b, "2026-11-10", 0.31, 4, 250.0)
    text = "\n".join(brake_lines(b))
    assert "고점 대비 −16.7% → TQQQ 3주 매도 $238.50" in text
    assert "매도 총액 $488.50" in text and "다음 매수 신호" in text
    # 평단 기준: 고점이 원가보다 낮으면 원가가 기준
    c = Brake(ref="H")
    add_deposit(c, 1000.0, 0.0)
    record_close(c, "2026-11-02", 840.0, 50.0)
    assert round(drawdown(c, 50.0) or 0, 2) == 0.16


def test_brake_t_and_rebuy_after_trend_off_then_on() -> None:
    b = Brake(ref="T")
    add_deposit(b, 1000.0, 0.0)
    record_close(b, "d1", 1000.0, 100.0)
    assert round(drawdown(b, 84.0) or 0, 2) == 0.16
    assert brake_sell(b, 0.16, 10) == 5
    sold(b, "d2", 0.16, 5, 420.0)
    assert not rebuy_due(b, True)  # 꺼진 적이 없으면 되사지 않는다
    assert not rebuy_due(b, False)
    assert rebuy_due(b, True)
    rebought(b, "d9", 900.0, 70.0)
    assert b.stage == 0 and b.reserve_usd == 0 and b.lev_peak == 70.0 and not b.sales
    assert b.sold_total_usd == 420.0
    assert Brake.from_dict(b.to_dict()) == b


def test_dividend_steps_and_shift_plan() -> None:
    divs = [["2026-03-20", "SCHD", 100, 0.25, 21.25], ["2025-01-01", "SCHD", 1, 1, 999.0]]
    m = monthly_dividend_krw(divs, "2026-10-09", 1400.0)
    assert round(m) == round(21.25 * 1400 / 12)
    assert step_due(0, 99_999) is None and step_due(0, 100_000) == 0 and step_due(4, 9e9) is None
    assert lev_cap(1) == 0.5
    sell, buy = shift_plan(lev_qty=40, lev_px=100.0, div_px=30.0, total_usd=6000.0, new_cap=0.5)
    assert (sell, buy) == (10, 33)  # 4,000 − 3,000 = 1,000 → 10 주 → SCHD 33 주
    msg = div_step_message(
        monthly_krw=102_300,
        step_krw=100_000,
        cap=0.6,
        new_cap=0.5,
        sell=4,
        lev_px=107.5,
        buy=13,
        fx=1340.0,
        added_krw=5_600,
        gain_krw=210_000,
        room_krw=2_500_000,
    )
    assert "TQQQ 목표 60% → 50%" in msg and "세금 0원" in msg and '"2주만"' in msg


def test_reply_parse_and_ack_with_cancel_window() -> None:
    assert parse_reply("그러면 2주를 팔아줘") == ("qty", 2)
    assert parse_reply("네") == ("yes", None) and parse_reply("응 진행해") == ("yes", None)
    assert parse_reply("아니요") == ("no", None) and parse_reply("다 팔아") == ("all", None)
    assert parse_reply("취소") == ("cancel", None) and parse_reply("음 글쎄") is None
    p = Proposal("p1", "harvest", "2026-12-14", "2026-12-29", "TQQQ", 3)
    assert "이해하지 못했어요" in answer(p, None, "2026-12-15", "10:00")
    msg = answer(p, ("qty", 5), "2026-12-15", "10:00")  # 최대 3 주로 줄임
    assert p.status == "accepted" and p.qty == 3 and "오늘 밤 TQQQ 3주" in msg and "20:50 전" in msg
    assert "취소했어요" in answer(p, ("cancel", None), "2026-12-15", "20:40")
    assert p.status == "open"
    answer(p, ("yes", None), "2026-12-15", "20:55")
    assert "이미 주문을 낸 뒤" in answer(p, ("cancel", None), "2026-12-15", "20:56")
    late = Proposal("p2", "harvest", "2026-12-14", "2026-12-29", "TQQQ", 2)
    assert "다음 거래일 밤" in answer(late, ("yes", None), "2026-12-15", "22:00")
    d = Proposal(
        "p3",
        "div_step",
        "2026-10-01",
        "2026-12-31",
        "TQQQ",
        4,
        {"cap": 0.6, "next_step_krw": 300_000},
    )
    assert "TQQQ 60%를 그대로" in answer(d, ("no", None), "2026-10-02", "09:10")
    assert "30만 원" in answer(
        Proposal("p4", "div_step", "a", "z", "TQQQ", 1, {"cap": 0.6, "next_step_krw": 300_000}),
        ("no", None),
        "b",
        "09:00",
    )


def test_reminders_expiry_and_harvest_deadline() -> None:
    d = Proposal("p", "div_step", "2026-10-01", "2026-12-31", "TQQQ", 4)
    assert not remind_due(d, "2026-10-07") and remind_due(d, "2026-10-08")
    h = Proposal("h", "harvest", "2026-12-14", "2026-12-29", "TQQQ", 2)
    assert not remind_due(h, "2026-12-15") and remind_due(h, "2026-12-27")  # 마감 2 일 전 한 번
    h.reminded = "2026-12-27"
    assert not remind_due(h, "2026-12-28") and not remind_due(h, "2026-12-30")
    assert expire(h, "2026-12-30") and h.status == "expired"
    assert harvest_deadline(2026) == "2026-12-28"
    msg = harvest_message(
        year=2026, room_krw=680_000, picks=[("TQQQ", 3, 650_000)], deadline="2026-12-29"
    )
    assert "공제 남은 680,000원" in msg and "TQQQ 3주" in msg and "2026-12-29 밤" in msg


def test_buttons_direct_input_then_number_and_cancel_button() -> None:
    p = Proposal("h26", "harvest", "2026-12-01", "2026-12-28", "TQQQ", 3)
    row = proposal_buttons(p)[0]
    assert [b["text"] for b in row] == ["전부 진행", "직접입력", "이번엔 안 함"]
    assert parse_callback(row[1]["callback_data"]) == ("h26", "input")
    assert parse_callback("h26:hack") is None
    text, buttons = press(p, "input", "2026-12-10", "10:00")
    assert "몇 주 팔까요" in text and "최대 3주" in text and buttons is None and p.awaiting_qty
    text, buttons = typed(p, "2", "2026-12-10", "10:01")  # 숫자만
    assert p.status == "accepted" and p.qty == 2 and "TQQQ 2주" in text
    assert buttons and buttons[0][0]["callback_data"] == "h26:cancel"
    text, buttons = press(p, "cancel", "2026-12-10", "20:30")
    assert "취소했어요" in text and p.status == "open" and buttons is None
    # 직접입력 대기가 아니면 숫자만은 못 알아듣는다
    assert "이해하지 못했어요" in typed(p, "2", "2026-12-11", "09:00")[0]
    text, _ = press(p, "all", "2026-12-11", "09:00")
    assert p.qty == 3 and "TQQQ 3주" in text
    q = Proposal("h27", "harvest", "2026-12-01", "2026-12-28", "TQQQ", 3)
    assert "올해는 공제 실현을 하지 않겠습니다" in press(q, "no", "2026-12-02", "09:00")[0]
