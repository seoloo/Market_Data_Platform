"""대시보드 데스크톱 앱 — 전용 창(Windows WebView2)으로 실거래 홈을 연다 (사용자 2026-10-09).

빌드: ``scripts\\build_desktop.cmd`` → ``dist\\AutoTrading.exe`` + 바탕화면 바로 가기.

* 실행마다 저장소의 최신 UI를 별도 로컬 서버로 띄운다. 오래된 8787 서버는 재사용/종료하지 않는다.
* OS가 고른 빈 포트를 사용하며 창을 닫거나 앱이 강제 종료되면 자기 UI 서버만 종료한다.
* ``--restart-dashboard`` 바로가기도 동일한 최신 화면 시작 경로다. 관리자 권한은 필요 없다.
* 주문 · 계좌 조회 없음.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from html import escape
from pathlib import Path
from uuid import uuid4

REPO_FILE = "repo_path.txt"
START_TIMEOUT_SEC = 20
"""빌드 때 저장소 위치를 적어 exe 안에 넣는 파일."""


def find_repo() -> Path | None:
    """exe 가 저장소 dist 안에 있으면 그 위, 아니면 빌드 때 적은 위치."""
    here = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "dashboard.py").exists():
            return parent
    bundle = Path(getattr(sys, "_MEIPASS", here.parent))
    try:
        baked = Path((bundle / REPO_FILE).read_text(encoding="utf-8").strip())
    except OSError:
        return None
    return baked if (baked / "scripts" / "dashboard.py").exists() else None


def status(url: str) -> int | None:
    """우리 화면 서버인지 확인한다. 장부/외부 시세를 읽지 않는 자산 경로만 사용한다."""
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            if not str(resp.headers.get("Server", "")).startswith("AutoTradingDashboard"):
                return None
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


@dataclass
class DashboardSession:
    """이번 앱이 만든 프로세스 핸들만 보유한다. 포트/PID 검색으로 다른 서버를 죽이지 않는다."""

    process: subprocess.Popen[bytes]
    folder: tempfile.TemporaryDirectory[str]
    url: str = ""

    def close(self) -> None:
        if self.process.poll() is None:
            # pythonw.exe may be a venv redirector. Let the actual child exit cooperatively.
            (Path(self.folder.name) / "closed").touch()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                self.process.wait(timeout=3)
        self.folder.cleanup()


def start_session(repo: Path) -> DashboardSession:
    """매번 최신 소스의 서버를 시작하고 자기 ready 파일/프로세스만 확인한다."""
    folder = tempfile.TemporaryDirectory(prefix="orbit-ui-")
    ready = Path(folder.name) / "ready.json"
    nonce = uuid4().hex
    pyw = repo / ".venv" / "Scripts" / "pythonw.exe"
    try:
        process = subprocess.Popen(
            [
                str(pyw),
                str(repo / "scripts" / "desktop_dashboard.py"),
                "--ready-file",
                str(ready),
                "--parent-pid",
                str(os.getpid()),
                "--nonce",
                nonce,
            ],
            cwd=repo,
            creationflags=0x08000000 if os.name == "nt" else 0,
            close_fds=True,
        )
    except OSError:
        folder.cleanup()
        raise
    session = DashboardSession(process, folder)
    try:
        deadline = time.monotonic() + START_TIMEOUT_SEC
        while time.monotonic() < deadline and process.poll() is None:
            try:
                raw = json.loads(ready.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = None
            if isinstance(raw, dict):
                port = raw.get("port")
                # Windows venv's redirector PID can differ from the actual interpreter PID.
                if type(port) is int and 0 < port < 65536 and raw.get("nonce") == nonce:
                    base = f"http://127.0.0.1:{port}"
                    if status(base + "/assets/orbit-obi.png") == 200:
                        session.url = base + "/live"
                        return session
            time.sleep(0.1)
        raise RuntimeError(
            "최신 대시보드를 시작하지 못했습니다. 저장소의 가상환경을 확인해 주세요."
        )
    except Exception:
        session.close()
        raise


def _error_page(msg: str) -> str:
    return (
        "<html><body style='font-family:sans-serif;padding:40px'>"
        f"<h2>오르빗</h2><p>{escape(msg)}</p>"
        "<p>오르빗을 다시 실행하거나 저장소 경로를 확인해 주세요.</p></body></html>"
    )


def main() -> int:
    import webview  # type: ignore[import-not-found]  # 빌드 환경(build\desktop-venv)에만

    repo = find_repo()
    session = None
    problem = "저장소 위치를 찾지 못했습니다." if repo is None else ""
    if repo is not None:
        try:
            session = start_session(repo)
        except (OSError, RuntimeError) as exc:
            problem = f"대시보드 시작 오류: {type(exc).__name__}"
    kwargs = {"url": session.url} if session else {"html": _error_page(problem)}
    try:
        webview.create_window(
            "오르빗", width=1536, height=1080, min_size=(420, 600), text_select=True, **kwargs
        )
        storage = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AutoTradingApp"
        webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage))
    finally:
        if session is not None:
            session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
