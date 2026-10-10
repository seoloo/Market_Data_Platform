"""대시보드 데스크톱 앱 — 전용 창(Windows WebView2)으로 실거래 홈을 연다 (사용자 2026-10-09).

빌드: ``scripts\\build_desktop.cmd`` → ``dist\\AutoTrading.exe`` + 바탕화면 바로 가기.

* exe 는 **창만** 띄우는 얇은 실행기다. 화면 내용은 저장소의 대시보드 서버(``scripts/dashboard.py``,
  127.0.0.1 · 읽기 전용)가 그린다. 그래서 화면 코드를 바꿔도 exe 를 다시 빌드할 필요가 없다.
* 서버가 꺼져 있으면 저장소 ``.venv`` 의 ``pythonw`` 로 창 없이 켠다. 창을 닫아도 서버는 남는다
  (브라우저로도 같이 본다). 끄기는 ``scripts\\stop_dashboard.cmd``.
* 주문 · 계좌 조회 없음.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8787
START = f"http://127.0.0.1:{PORT}/live"
REPO_FILE = "repo_path.txt"
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


def status(url: str = START) -> int | None:
    """그 주소의 HTTP 상태 (꺼져 있으면 None)."""
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return exc.code
    except OSError:
        return None


def start_server(repo: Path, restart: bool = False) -> None:
    flags = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW
    if restart:
        subprocess.run(
            ["cmd", "/c", str(repo / "scripts" / "stop_dashboard.cmd")],
            cwd=repo,
            creationflags=0x08000000,
            check=False,
        )
        time.sleep(1.5)  # 포트가 풀릴 때까지
    pyw = repo / ".venv" / "Scripts" / "pythonw.exe"
    subprocess.Popen(
        [str(pyw), str(repo / "scripts" / "dashboard.py"), "--no-browser", "--port", str(PORT)],
        cwd=repo,
        creationflags=flags,
        close_fds=True,
    )


def ensure_server(repo: Path) -> str | None:
    """서버를 띄우고 실거래 홈이 열리는지 본다. 문제가 있으면 한 줄 이유."""
    code = status()
    if code == 200:
        return None
    if code == 404:  # 실거래 홈이 없던 예전 서버 → 다시 켠다
        start_server(repo, restart=True)
    elif code is None:
        start_server(repo)
    for _ in range(40):
        time.sleep(0.5)
        if status() == 200:
            return None
    return f"대시보드 서버가 응답하지 않습니다 ({START})"


def _error_page(msg: str) -> str:
    return (
        "<html><body style='font-family:sans-serif;padding:40px'>"
        f"<h2>오르빗</h2><p>{msg}</p>"
        "<p>저장소의 scripts\\open_dashboard.cmd 로 직접 켜 보세요.</p></body></html>"
    )


def main() -> int:
    import webview  # type: ignore[import-not-found]  # 빌드 환경(build\desktop-venv)에만

    repo = find_repo()
    problem = "저장소 위치를 찾지 못했습니다." if repo is None else ensure_server(repo)
    kwargs = {"html": _error_page(problem)} if problem else {"url": START}
    webview.create_window(
        "오르빗", width=1536, height=1080, min_size=(420, 600), text_select=True, **kwargs
    )
    storage = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AutoTradingApp"
    webview.start(gui="edgechromium", private_mode=False, storage_path=str(storage))
    return 0


if __name__ == "__main__":
    sys.exit(main())
