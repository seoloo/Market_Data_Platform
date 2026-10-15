"""설정 '연결 시험' 을 따로 띄워 읽는 경로 — 결과 줄은 ASCII JSON (콘솔 인코딩 무관)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_trading.ops.conn_test import main


def test_cli_prints_ascii_json_last_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(tmp_path), "nothing"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.isascii()
    assert json.loads(line) == [False, "시험할 수 없는 항목이에요"]
