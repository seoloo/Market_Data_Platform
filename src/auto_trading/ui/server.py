"""대시보드 HTTP 서버 — 표준 라이브러리만, **127.0.0.1 전용, GET + 버튼 하나**.

보관 설계 D10(`docs/archive/design/14-ui-and-packaging.md`)의 안전 원칙을 따른다.

* 별도 프로세스: 매매·Paper 실행기와 따로 돈다. 죽어도 매매에 닿지 않는다.
* 읽기 전용: 요청마다 DB 를 ``mode=ro`` 로 열고 닫는다.
* **예외 하나** (사용자 요청 2026-10-02): ``POST /catchup`` — 놓친 예약 작업을 작업 스케줄러로
  다시 시작한다(ui/catchup.py). 받는 값은 화면에 심은 토큰뿐이고, 무엇을 돌릴지는 서버가 정한다.
  같은 출처(Origin·Host)와 프로세스마다 새로 만든 토큰이 맞아야 한다. PUT·DELETE 등은 501.
* 외부 노출 금지: 127.0.0.1·localhost 만 바인딩한다.
* API 키를 읽지 않는다. 거래소는 실거래 홈의 **공개 시세**(키 없음 · 60 초 캐시)만
  부른다(2026-10-09).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from auto_trading.ui.catchup import CatchupRunner
from auto_trading.ui.data import DashboardData, load_dashboard
from auto_trading.ui.orbit_icons import ICON_ASSETS
from auto_trading.ui.render import PAGES, render_page

__all__ = ["ALLOWED_HOSTS", "DashboardServer", "make_server"]

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})
_ROUTES = {href: key for key, href, _ in PAGES}
_ROUTES.update({"/overseas": "toss", "/accumulate": "toss", "/qndx": "toss"})
_ASSET_DIR = Path(__file__).with_name("assets")
_ASSETS = {
    "/favicon.ico": ("auto-trading.ico", "image/x-icon"),
    "/assets/auto-trading.ico": ("auto-trading.ico", "image/x-icon"),
    "/assets/auto-trading.svg": ("auto-trading.svg", "image/svg+xml"),
    "/assets/obi.png": ("obi.png", "image/png"),
    "/assets/orbit-obi.png": ("orbit-obi.png", "image/png"),
    "/assets/orbit-hero-light.png": ("orbit-hero-light.png", "image/png"),
    "/assets/orbit-hero-dark-sleep.png": ("orbit-hero-dark-sleep.png", "image/png"),
    "/assets/orbit-space-dark.png": ("orbit-space-dark.png", "image/png"),
    "/assets/orbit-space-light.png": ("orbit-space-light.png", "image/png"),
}
_ASSETS.update(
    {
        f"/assets/icons/{name}": (
            f"icons/{name}",
            {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}[
                Path(name).suffix
            ],
        )
        for name in ICON_ASSETS
    }
)
_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # same-origin: 버튼 POST 에 Origin 이 실리게 (no-referrer 면 "null"). 바깥으로는 안 보낸다.
    "Referrer-Policy": "same-origin",
    # 인라인 스타일만 쓴다. 스크립트·외부 자원 없음. 폼은 자기 자신(/catchup)으로만.
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}

Loader = Callable[[], DashboardData]


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], loader: Loader, catchup: CatchupRunner | None = None
    ) -> None:
        self.loader = loader
        self.catchup = catchup
        super().__init__(address, _Handler)

    def same_origin(self, origin: str | None, host: str | None) -> bool:
        port = self.server_address[1]
        allowed = {f"{h}:{port}" for h in ALLOWED_HOSTS}
        return host in allowed and origin in {f"http://{a}" for a in allowed}


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "AutoTradingDashboard"
    sys_version = ""

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        asset = _ASSETS.get(path)
        if asset is not None:
            try:
                body = (_ASSET_DIR / asset[0]).read_bytes()
            except OSError:
                self._send(404, "<!doctype html><p>아이콘 없음</p>")
                return
            self._send_bytes(200, body, asset[1])
            return
        page = _ROUTES.get(path)
        if page is None:
            self._send(404, "<!doctype html><meta charset='utf-8'><p>없는 화면</p>")
            return
        try:
            data = self.server.loader()
            if page == "running" and self.server.catchup is not None:
                data = replace(data, catchup=self.server.catchup.view())
            query = parse_qs(self.path.partition("?")[2])
            selected = (query.get("theme") or [""])[0]
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                cookie = SimpleCookie()
            saved = cookie.get("dashboard_theme")
            theme = selected if selected in {"light", "dark"} else saved.value if saved else "light"
            html = render_page(data, page, theme=theme)
        except Exception as exc:  # 화면 오류가 서버를 멈추지 않게 — 원인만 보인다
            self._send(
                500, f"<!doctype html><meta charset='utf-8'><p>화면 오류: {type(exc).__name__}</p>"
            )
            return
        self._send(200, html, theme=selected if selected in {"light", "dark"} else None)

    def do_POST(self) -> None:
        """놓친 작업 다시 실행 버튼 하나만 받는다."""
        if self.path != "/catchup" or self.server.catchup is None:
            self._send(404, "<!doctype html><meta charset='utf-8'><p>없는 화면</p>")
            return
        if not self.server.same_origin(self.headers.get("Origin"), self.headers.get("Host")):
            self._send(403, "<!doctype html><meta charset='utf-8'><p>다른 출처의 요청</p>")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 1024:
            self._send(400, "<!doctype html><meta charset='utf-8'><p>잘못된 요청</p>")
            return
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        token = (form.get("token") or [""])[0]
        if not secrets.compare_digest(token, self.server.catchup.token):
            self._send(403, "<!doctype html><meta charset='utf-8'><p>화면을 새로 고친 뒤 다시</p>")
            return
        self.server.catchup.start()
        self.send_response(303)
        self.send_header("Location", "/running")
        self.send_header("Content-Length", "0")
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def _send(self, status: int, html: str, *, theme: str | None = None) -> None:
        self._send_bytes(status, html.encode("utf-8"), "text/html; charset=utf-8", theme=theme)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        theme: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if theme in {"light", "dark"}:
            self.send_header(
                "Set-Cookie",
                f"dashboard_theme={theme}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict",
            )
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return  # 접속 기록을 남기지 않는다 (콘솔 잡음)


def make_server(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    catchup: CatchupRunner | None = None,
) -> DashboardServer:
    """``catchup`` 을 주지 않으면 실제 작업 스케줄러를 쓰는 실행기를 만든다."""
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"대시보드는 127.0.0.1 전용이다: {host}")

    def loader() -> DashboardData:
        return load_dashboard(root, int(time.time() * 1000))

    return DashboardServer((host, port), loader, catchup or CatchupRunner(root))
