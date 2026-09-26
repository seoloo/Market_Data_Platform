"""대시보드 HTTP 서버 — 표준 라이브러리만, **127.0.0.1 전용, GET 만**.

보관 설계 D10(`docs/archive/design/14-ui-and-packaging.md`)의 안전 원칙을 따른다.

* 별도 프로세스: 매매·Paper 실행기와 따로 돈다. 죽어도 매매에 닿지 않는다.
* 읽기 전용: 요청마다 DB 를 ``mode=ro`` 로 열고 닫는다. 쓰기 경로·쓰기 엔드포인트가 없다
  (``do_POST`` 등이 없어 표준 라이브러리가 501 로 거부한다).
* 외부 노출 금지: 127.0.0.1·localhost 만 바인딩한다.
* API 키를 읽지 않고 거래소를 호출하지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from auto_trading.ui.data import DashboardData, load_dashboard
from auto_trading.ui.render import PAGES, render_page

__all__ = ["ALLOWED_HOSTS", "DashboardServer", "make_server"]

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
_ROUTES = {href: key for key, href, _ in PAGES}
_ROUTES.update({"/accumulate": "overseas", "/qndx": "overseas"})
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # 인라인 스타일만 쓴다. 스크립트·외부 자원·폼 전송 없음.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

Loader = Callable[[], DashboardData]


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], loader: Loader) -> None:
        self.loader = loader
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "AutoTradingDashboard"
    sys_version = ""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        page = _ROUTES.get(path)
        if page is None:
            self._send(404, "<!doctype html><meta charset='utf-8'><p>없는 화면</p>")
            return
        try:
            html = render_page(self.server.loader(), page)
        except Exception as exc:  # 화면 오류가 서버를 멈추지 않게 — 원인만 보인다
            self._send(
                500, f"<!doctype html><meta charset='utf-8'><p>화면 오류: {type(exc).__name__}</p>"
            )
            return
        self._send(200, html)

    def _send(self, status: int, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return  # 접속 기록을 남기지 않는다 (콘솔 잡음)


def make_server(root: Path, host: str = "127.0.0.1", port: int = 8787) -> DashboardServer:
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"대시보드는 127.0.0.1 전용이다: {host}")

    def loader() -> DashboardData:
        return load_dashboard(root, int(time.time() * 1000))

    return DashboardServer((host, port), loader)
