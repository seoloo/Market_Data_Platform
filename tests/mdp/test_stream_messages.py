"""스트림 메시지 시험 — 재생 메시지 모양 · 결함 섞기 (Kafka · Spark 없이)."""

from __future__ import annotations

import json

import pytest

from mdp.stream.messages import Delivery, Faults, apply_faults, replay_message

ROW = ["2041763940", "61203.40", "0.663", "4659223535", "4659223584", "1709251200019", "false"]


def test_replay_message_has_live_shape_and_keeps_number_text() -> None:
    time_ms, payload = replay_message("BTCUSDT", ROW)
    message = json.loads(payload)
    assert time_ms == 1709251200019
    assert message == {
        "e": "aggTrade",
        "E": 1709251200019,
        "a": 2041763940,
        "s": "BTCUSDT",
        "p": "61203.40",  # 끝 0 까지 글자 그대로
        "q": "0.663",
        "f": 4659223535,
        "l": 4659223584,
        "T": 1709251200019,
        "m": False,
    }
    assert json.loads(replay_message("BTCUSDT", [*ROW[:6], "True"])[1])["m"] is True


def _stream(n: int, step_ms: int = 1000) -> list[tuple[int, bytes]]:
    return [(i * step_ms, str(i).encode()) for i in range(1, n + 1)]


def test_no_faults_keeps_everything_in_order() -> None:
    out = list(apply_faults(_stream(5), Faults()))
    assert [t for t, _, _ in out] == [1000, 2000, 3000, 4000, 5000]
    assert {kind for _, _, kind in out} == {Delivery.NORMAL}


def test_duplicates_follow_the_original() -> None:
    out = list(apply_faults(_stream(6), Faults(duplicate_every=3)))
    assert [p for _, p, _ in out] == [b"1", b"2", b"3", b"3", b"4", b"5", b"6", b"6"]
    assert [k for _, _, k in out].count(Delivery.DUPLICATE) == 2


def test_delayed_message_arrives_after_later_trades() -> None:
    # 짝수 번째를 2.5 초 늦춤 → 2(2 초)는 흐름이 4.5 초를 지난 5 초 메시지 앞에, 4 · 6 은 끝에
    out = list(apply_faults(_stream(6), Faults(delay_every=2, delay_ms=2500)))
    order = [p for _, p, _ in out]
    assert order == [b"1", b"3", b"2", b"5", b"4", b"6"]
    assert sorted(order) == [b"1", b"2", b"3", b"4", b"5", b"6"]  # 잃지도 늘지도 않음
    delayed = [(t, p) for t, p, k in out if k is Delivery.DELAYED]
    assert (2000, b"2") in delayed  # 체결 시각은 원래 값 그대로


def test_delay_releases_exactly_when_due() -> None:
    out = [p for _, p, _ in apply_faults(_stream(5), Faults(delay_every=2, delay_ms=2000))]
    # 2(2 초)는 4 초에 나올 차례 → 4 는 늦춰지므로 5(5 초) 앞에서 2 가 나온다.
    # 4(4 초)는 6 초 → 끝에.
    assert out == [b"1", b"3", b"2", b"5", b"4"]


def test_faults_are_deterministic() -> None:
    faults = Faults(duplicate_every=3, delay_every=4, delay_ms=1500)
    assert list(apply_faults(_stream(50), faults)) == list(apply_faults(_stream(50), faults))


@pytest.mark.parametrize("kwargs", [{"duplicate_every": -1}, {"delay_every": 3}, {"delay_ms": -5}])
def test_bad_fault_settings_stop(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        Faults(**kwargs)
