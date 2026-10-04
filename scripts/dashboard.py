"""운영 대시보드 (읽기 전용) 실행.

사용 예::

    python scripts/dashboard.py                  # http://127.0.0.1:8787 를 열고 브라우저를 띄운다
    python scripts/dashboard.py --no-browser     # 서버만
    python scripts/dashboard.py --export out     # 서버 없이 화면들을 HTML 파일로 저장

127.0.0.1 전용·GET 만·DB 읽기 전용이다. 매매·Paper 실행기와 따로 돌고, 꺼도 아무 영향이 없다.
끌 때는 이 창에서 Ctrl+C.

창 없이 띄우기(바탕화면 바로 가기·``scripts/open_dashboard.cmd``)는 ``pythonw`` 로 실행한다.
이미 떠 있으면 새 서버를 만들지 않고 브라우저만 연다 — 몇 번을 눌러도 서버는 하나다.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from auto_trading.ui.data import load_dashboard
from auto_trading.ui.render import PAGES, render_page
from auto_trading.ui.server import make_server

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent


def export(folder: Path) -> int:
    data = load_dashboard(REPO, int(time.time() * 1000))
    folder.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        suffix = "" if theme == "light" else "-dark"
        for key, href, _ in PAGES:
            stem = "index" if href == "/" else href.strip("/")
            html = render_page(data, key, theme=theme)
            for _, other_href, _ in PAGES:  # 파일끼리 이동되게 링크를 바꾼다
                target = "index" if other_href == "/" else other_href.strip("/")
                for selected in ("light", "dark"):
                    target_suffix = "" if selected == "light" else "-dark"
                    html = html.replace(
                        f'href="{other_href}?theme={selected}"',
                        f'href="{target}{target_suffix}.html"',
                    )
                html = html.replace(f'href="{other_href}"', f'href="{target}{suffix}.html"')
                html = html.replace(f'href="{other_href}#', f'href="{target}{suffix}.html#')
            (folder / f"{stem}{suffix}.html").write_text(html, encoding="utf-8", newline="\n")
    print(f"화면 {len(PAGES)}개 × 2개 테마 저장: {folder}")
    if data.errors:
        print("읽기 실패:", *data.errors, sep="\n  ")
    return 0


def already_running(port: int) -> bool:
    """그 포트에 이 대시보드가 이미 떠 있는가 (다른 프로그램이면 False)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/running", timeout=2) as resp:
            return str(resp.headers.get("Server", "")).startswith("AutoTradingDashboard")
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--export", type=Path, help="서버 없이 HTML 파일로 저장할 폴더")
    args = parser.parse_args()
    if args.export:
        return export(args.export)
    if already_running(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"대시보드가 이미 떠 있다: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    server = make_server(REPO, port=args.port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"대시보드: {url}  (읽기 전용 · 끄기 Ctrl+C)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
