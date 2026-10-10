"""데스크톱의 최신 코드 시작/다중 창/자기 서버 정리 계약. 매매·계좌 요청 없음."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from scripts import desktop_app, desktop_dashboard

ROOT = Path(__file__).resolve().parents[2]


def test_error_text_is_escaped() -> None:
    assert "<script>" not in desktop_app._error_page("<script>alert(1)</script>")
    assert "&lt;script&gt;" in desktop_app._error_page("<script>")


def test_missing_python_cleans_temporary_folder(tmp_path: Path, monkeypatch) -> None:
    folders = []
    original = tempfile.TemporaryDirectory

    def temporary(**kwargs):
        folder = original(**kwargs)
        folders.append(Path(folder.name))
        return folder

    monkeypatch.setattr(tempfile, "TemporaryDirectory", temporary)
    with pytest.raises(OSError):
        desktop_app.start_session(tmp_path)
    assert len(folders) == 1 and not folders[0].exists()


def test_server_bind_is_ephemeral_and_no_account_read(tmp_path: Path) -> None:
    ready = tmp_path / "ready.json"
    observed = []

    def wait() -> None:
        data = json.loads(ready.read_text())
        assert data["pid"] == os.getpid()
        port = data["port"]
        observed.append(port)
        assert port != 0
        assert desktop_app.status(f"http://127.0.0.1:{port}/assets/orbit-obi.png") == 200
        assert not (tmp_path / "data").exists()

    desktop_dashboard.run(tmp_path, ready, wait)
    assert not ready.exists()
    assert desktop_app.status(f"http://127.0.0.1:{observed[0]}/assets/orbit-obi.png") is None


def test_wait_error_closes_only_own_server(tmp_path: Path) -> None:
    ready = tmp_path / "ready.json"
    port = []

    def wait() -> None:
        port.append(json.loads(ready.read_text())["port"])
        raise RuntimeError("injected wait failure")

    with pytest.raises(RuntimeError, match="injected"):
        desktop_dashboard.run(tmp_path, ready, wait)
    assert not ready.exists()
    assert desktop_app.status(f"http://127.0.0.1:{port[0]}/assets/orbit-obi.png") is None


def test_parent_pid_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        desktop_dashboard.parent_waiter(0, tmp_path / "closed")


@pytest.mark.skipif(os.name != "nt", reason="Windows pythonw lifecycle")
def test_actual_sessions_do_not_reuse_old_or_other_window() -> None:
    old_status = desktop_app.status("http://127.0.0.1:8787/assets/obi.png")
    first = desktop_app.start_session(ROOT)
    second = None
    try:
        second = desktop_app.start_session(ROOT)
        assert first.url != second.url
        assert ":8787/" not in first.url
        first.close()
        assert desktop_app.status(second.url.replace("/live", "/assets/orbit-obi.png")) == 200
        assert desktop_app.status("http://127.0.0.1:8787/assets/obi.png") == old_status
    finally:
        first.close()
        if second:
            second.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows parent process handle")
def test_forced_parent_exit_stops_own_child(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    code = (
        "import json,time; from pathlib import Path; "
        "from scripts.desktop_app import start_session; "
        f"s=start_session(Path({str(ROOT)!r})); "
        f"Path({str(report)!r}).write_text(json.dumps(dict(url=s.url, folder=s.folder.name))); "
        "time.sleep(30)"
    )
    parent = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT)
    try:
        deadline = time.monotonic() + 15
        while not report.exists() and parent.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        data = json.loads(report.read_text())
        asset = data["url"].replace("/live", "/assets/orbit-obi.png")
        assert desktop_app.status(asset) == 200
        parent.terminate()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 6
        while desktop_app.status(asset) == 200 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert desktop_app.status(asset) is None
        assert not (Path(data["folder"]) / "ready.json").exists()
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=3)


@pytest.mark.skipif(os.name != "nt", reason="Windows pythonw lifecycle")
def test_timeout_cleans_owned_child(monkeypatch) -> None:
    monkeypatch.setattr(desktop_app, "START_TIMEOUT_SEC", 0)
    with pytest.raises(RuntimeError, match="최신 대시보드"):
        desktop_app.start_session(ROOT)


def test_png_and_ico_keep_alpha() -> None:
    assets = ROOT / "src/auto_trading/ui/assets"
    assert (assets / "orbit-icon-source.png").read_bytes()[25] == 6
    assert (assets / "obi.png").read_bytes()[25] == 6
    ico = (assets / "orbit-transparent.ico").read_bytes()
    assert ico[:6] == b"\x00\x00\x01\x00\x07\x00"
    for index in range(7):
        entry = 6 + index * 16
        start = int.from_bytes(ico[entry + 12 : entry + 16], "little")
        assert ico[start : start + 8] == b"\x89PNG\r\n\x1a\n"
        assert ico[start + 25] == 6


def test_restart_does_not_stop_trade_or_privileged_server() -> None:
    cmd = (ROOT / "scripts/restart_dashboard.cmd").read_text()
    assert "stop_dashboard" not in cmd
    assert "--restart-dashboard" in cmd
    source = (ROOT / "scripts/desktop_app.py").read_text(encoding="utf-8")
    assert "stop_dashboard.cmd" not in source
    assert "Get-CimInstance" not in source
    assert "RunAs" not in source
