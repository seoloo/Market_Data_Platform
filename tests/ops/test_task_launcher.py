"""창 없는 예약 작업 실행기 (scripts/task_launcher.py) — 출력은 기록 파일로, 종료 코드는 그대로."""

from __future__ import annotations

from pathlib import Path

import pytest
import scripts.task_launcher as launcher


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ("print('hi')", 0),
        ("raise SystemExit(2)", 2),
        ("raise SystemExit('bad usage')", 1),
        ("1/0", 1),
    ],
)
def test_exit_code_and_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str, code: int
) -> None:
    monkeypatch.setattr(launcher, "REPO", tmp_path)
    script = tmp_path / "job.py"
    script.write_text(f"import sys\nprint('args', sys.argv[1:])\n{body}\n", encoding="utf-8")
    assert launcher.main([str(script), "run", "--x"]) == code
    (log,) = (tmp_path / "reports" / "tasklog").glob("*_job_run_x.log")
    text = log.read_text(encoding="utf-8")
    assert "args ['run', '--x']" in text and f"종료 코드 {code}" in text
    if body == "1/0":
        assert "ZeroDivisionError" in text


def test_module_target(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(launcher, "REPO", tmp_path)
    pkg = tmp_path / "pkgx"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("import sys\nprint('mod', sys.argv[1:])\n", encoding="utf-8")
    assert launcher.main(["-m", "pkgx.mod", "daily"]) == 0
    (log,) = (tmp_path / "reports" / "tasklog").glob("*_mod_daily.log")
    assert "mod ['daily']" in log.read_text(encoding="utf-8")
