"""샘플 시연 시험 — 합성 Bronze 가 늘 같고, 넣은 결함 · 정답지가 서로 맞는지.

Spark · Docker 없이 돈다.
"""

from __future__ import annotations

import csv
import hashlib
import io
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from mdp.bronze.fetch import read_manifest
from mdp.sample.demo import _clear, plan_steps
from mdp.sample.generate import SYMBOL, generate


@pytest.fixture(scope="module")
def sample(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("sample")
    return root, generate(root)


def _rows(root: Path, relative_path: str) -> list[list[str]]:
    zip_path = root / "bronze" / relative_path
    with zipfile.ZipFile(zip_path) as archive:
        (name,) = archive.namelist()
        assert name == zip_path.with_suffix(".csv").name  # Silver 가 요구하는 이름
        text = archive.read(name).decode("utf-8")
    return list(csv.reader(io.StringIO(text)))


def test_manifest_matches_files_and_is_deterministic(
    sample: tuple[Path, dict[str, Any]], tmp_path: Path
) -> None:
    root, _ = sample
    manifest = read_manifest(root / "bronze" / "manifest.csv")
    assert len(manifest) == 6  # 3 일 × (체결 · 1 분봉)
    for relative_path, (size, sha256) in manifest.items():
        data = (root / "bronze" / relative_path).read_bytes()
        assert (len(data), hashlib.sha256(data).hexdigest()) == (size, sha256)
    generate(tmp_path)  # 다시 만들어도 같은 바이트
    assert read_manifest(tmp_path / "bronze" / "manifest.csv") == manifest


def test_defects_are_only_on_day_two(sample: tuple[Path, dict[str, Any]]) -> None:
    root, expected = sample
    base = f"data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-2024-01-0"
    for day in (1, 3):
        rows = _rows(root, f"{base}{day}.zip")[1:]
        ids = [int(r[0]) for r in rows]
        assert ids == list(range(ids[0], ids[0] + len(ids)))  # 빈 곳 · 중복 없음
    day2 = _rows(root, f"{base}2.zip")[1:]
    parsed = [r for r in day2 if r[0].isdigit()]
    assert len(day2) - len(parsed) == 1  # 못 읽는 줄 하나
    repeats = Counter(r[0] for r in parsed)
    assert sum(n - 1 for n in repeats.values()) == expected["silver"]["duplicate_rows"]
    ids = sorted({int(r[0]) for r in parsed})
    gap = expected["silver"]["gaps"][0]
    assert all(i not in ids for i in range(gap["prev_id"] + 1, gap["next_id"]))
    assert gap["prev_id"] in ids and gap["next_id"] in ids


def test_klines_are_computed_from_true_trades(sample: tuple[Path, dict[str, Any]]) -> None:
    root, expected = sample
    kline = f"data/futures/um/daily/klines/{SYMBOL}/1m/{SYMBOL}-1m-2024-01-01.zip"
    trades = f"data/futures/um/daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-2024-01-01.zip"
    klines = _rows(root, kline)[1:]
    assert len(klines) == 1440
    # 하루 개별 체결 수 = 묶음마다 (끝 번호 − 첫 번호 + 1) 의 합
    fills = sum(int(r[4]) - int(r[3]) + 1 for r in _rows(root, trades)[1:])
    assert sum(int(k[8]) for k in klines) == fills
    assert expected["gold"]["kline_all_fields_equal"]["2024-01-01"] == 1440


def test_answer_sheet_is_self_consistent(sample: tuple[Path, dict[str, Any]]) -> None:
    _, expected = sample
    silver, gold = expected["silver"], expected["gold"]
    lost = expected["true_agg_trades"] - silver["clean_rows"]
    assert lost == silver["missing_ids_inside_day"]["2024-01-02"] == 6  # 빈 곳 5 + 다른 내용 중복 1
    assert 1440 - gold["kline_all_fields_equal"]["2024-01-02"] == len(gold["lost_minutes_ms"])
    assert expected["stream"]["stream_gap_minutes"] == 6  # 12:00 ~ 12:05


def test_demo_plan_uses_same_commands_and_optional_stream(tmp_path: Path) -> None:
    batch = plan_steps(tmp_path, with_stream=False, build_image=False)
    full = plan_steps(tmp_path, with_stream=True, build_image=True)
    joined = [" ".join(s.command) for s in batch]
    assert any("mdp.silver.agg_trades" in c for c in joined)
    assert any("mdp.gold.bars_1m" in c for c in joined)
    assert not any("mdp.stream" in c for c in joined)
    assert len(full) == len(batch) + 5  # 이미지 만들기 + Kafka 4 단계
    assert full[-1].command[-3:] == ("mdp.sample.verify", "--root", str(tmp_path))


def test_clear_refuses_folders_outside_data_or_without_answer_sheet(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="아래여야"):
        _clear(tmp_path)
