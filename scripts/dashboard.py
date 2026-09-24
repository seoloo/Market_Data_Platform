"""운영 대시보드 (읽기 전용) 실행.

사용 예::

    python scripts/dashboard.py                  # http://127.0.0.1:8787 를 열고 브라우저를 띄운다
    python scripts/dashboard.py --no-browser     # 서버만
    python scripts/dashboard.py --export out     # 서버 없이 화면 6개를 HTML 파일로 저장

127.0.0.1 전용·GET 만·DB 읽기 전용이다. 매매·Paper 실행기와 따로 돌고, 꺼도 아무 영향이 없다.
끌 때는 이 창에서 Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
import time
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
    for key, href, _ in PAGES:
        name = "index.html" if href == "/" else f"{href.strip('/')}.html"
        html = render_page(data, key)
        for _, other_href, _ in PAGES:  # 파일끼리 이동되게 링크를 바꾼다
            target = "index.html" if other_href == "/" else f"{other_href.strip('/')}.html"
            html = html.replace(f'href="{other_href}"', f'href="{target}"')
        (folder / name).write_text(html, encoding="utf-8", newline="\n")
    print(f"화면 {len(PAGES)}개 저장: {folder}")
    if data.errors:
        print("읽기 실패:", *data.errors, sep="\n  ")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--export", type=Path, help="서버 없이 HTML 파일로 저장할 폴더")
    args = parser.parse_args()
    if args.export:
        return export(args.export)
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
