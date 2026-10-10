"""대시보드 HTTP 서버 — 표준 라이브러리만, **127.0.0.1 전용, GET + 버튼 하나**.

보관 설계 D10(`docs/archive/design/14-ui-and-packaging.md`)의 안전 원칙을 따른다.

* 별도 프로세스: 매매·Paper 실행기와 따로 돈다. 죽어도 매매에 닿지 않는다.
* 읽기 전용: 요청마다 DB 를 ``mode=ro`` 로 열고 닫는다.
* **예외 둘째** (사용자 2026-10-10 시안 확정): ``POST /settings/review`` · ``/settings/save`` —
  설정 화면. 같은 출처 + 프로세스마다 새 토큰 + 확인 화면을 거쳐야 저장된다. 쓰는 곳은
  ``data/settings/`` 와 비상 정지 ``data/HALT`` 뿐 (ops/user_settings.py). 매매 규칙은 못 바꾼다.
* **예외 하나** (사용자 요청 2026-10-02): ``POST /catchup`` — 놓친 예약 작업을 작업 스케줄러로
  다시 시작한다(ui/catchup.py). 받는 값은 화면에 심은 토큰뿐이고, 무엇을 돌릴지는 서버가 정한다.
  같은 출처(Origin·Host)와 프로세스마다 새로 만든 토큰이 맞아야 한다. PUT·DELETE 등은 501.
* 외부 노출 금지: 127.0.0.1·localhost 만 바인딩한다.
* API 키를 읽지 않는다. 거래소는 실거래 홈의 **공개 시세**(키 없음 · 60 초 캐시)만
  부른다(2026-10-09).
* **스크립트 예외 하나** (2026-10-10, 화면 개선): 실거래 홈(``/live``)만 같은 서버의
  ``/assets/orbit-live.js`` 하나를 허용한다 — 숫자 칸만 60 초마다 새로 고치는 읽기 전용 스크립트.
  인라인 스크립트 · 외부 스크립트는 여전히 막는다(CSP ``script-src 'self'``, 다른 화면은 그대로).
* 실거래 홈 쿠키 셋: ``dashboard_amounts`` (금액 숨기기) · ``dashboard_onboard`` (첫 안내 봄) ·
  ``dashboard_detail`` (세 카드 보유 상세 펼침).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from auto_trading.ops import user_settings
from auto_trading.ops.kr_accumulate import read_universe
from auto_trading.ui.catchup import CatchupRunner
from auto_trading.ui.data import DashboardData, load_dashboard
from auto_trading.ui.orbit_home import layout as orbit_layout
from auto_trading.ui.orbit_icons import ICON_ASSETS
from auto_trading.ui.render import PAGES, render_page
from auto_trading.ui.settings_page import render_review, render_settings

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
    "/assets/orbit-live.js": ("orbit-live.js", "text/javascript; charset=utf-8"),
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

_LIVE_CSP = _HEADERS["Content-Security-Policy"].replace(
    "default-src 'none';", "default-src 'none'; script-src 'self'; connect-src 'self';"
)
"""실거래 홈만: 같은 서버 스크립트 파일 하나 · 같은 서버 fetch. 인라인 스크립트는 여전히 막힌다."""

Loader = Callable[[], DashboardData]


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        loader: Loader,
        catchup: CatchupRunner | None = None,
        root: Path | None = None,
    ) -> None:
        self.loader = loader
        self.catchup = catchup
        self.root = root
        self.settings_token = secrets.token_urlsafe(24)
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
        if path == "/settings" and self.server.root is not None:
            query = parse_qs(self.path.partition("?")[2])
            saved_n = (query.get("saved") or [""])[0]
            notice = f"저장했어요 · 바뀐 값 {saved_n}개" if saved_n.isdigit() else ""
            pick = (query.get("theme") or [""])[0]
            self._settings_page(
                user_settings.load(self.server.root),
                {},
                notice,
                theme=pick if pick in {"light", "dark"} else None,
            )
            return
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
            prefs = user_settings.load(self.server.root) if self.server.root else {}
            default_theme = (
                prefs.get("screen.theme") if prefs.get("screen.theme") != "system" else ""
            )
            theme = (
                selected
                if selected in {"light", "dark"}
                else saved.value
                if saved
                else default_theme or "light"
            )
            cookies: list[str] = []
            if page == "live":
                amounts = (query.get("amounts") or [""])[0]
                onboard = (query.get("onboard") or [""])[0]
                saved_amounts = cookie.get("dashboard_amounts")
                hide = (
                    amounts == "hide"
                    if amounts in {"hide", "show"}
                    else saved_amounts.value == "hide"
                    if saved_amounts
                    else bool(prefs.get("screen.hide_amounts"))
                )
                seen = cookie.get("dashboard_onboard")
                onboarded = (
                    onboard == "done"
                    if onboard in {"done", "again"}
                    else bool(seen and seen.value == "done")
                )
                if amounts in {"hide", "show"}:
                    cookies.append(f"dashboard_amounts={amounts}")
                if onboard in {"done", "again"}:
                    cookies.append(f"dashboard_onboard={'done' if onboard == 'done' else 'again'}")
                detail = (query.get("detail") or [""])[0]
                saved_detail = cookie.get("dashboard_detail")
                show = (
                    detail == "open"
                    if detail in {"open", "close"}
                    else bool(saved_detail and saved_detail.value == "open")
                )
                if detail in {"open", "close"}:
                    cookies.append(f"dashboard_detail={detail}")
                data = replace(data, hide_amounts=hide, onboarded=onboarded, show_detail=show)
            html = render_page(data, page, theme=theme)
        except Exception as exc:  # 화면 오류가 서버를 멈추지 않게 — 원인만 보인다
            self._send(
                500, f"<!doctype html><meta charset='utf-8'><p>화면 오류: {type(exc).__name__}</p>"
            )
            return
        self._send(
            200,
            html,
            theme=selected if selected in {"light", "dark"} else None,
            cookies=cookies,
            csp=_LIVE_CSP if page == "live" else None,
        )

    def _theme(self) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return "light"
        saved = cookie.get("dashboard_theme")
        if saved and saved.value in {"light", "dark"}:
            return saved.value
        pref = user_settings.get(self.server.root, "screen.theme") if self.server.root else ""
        return pref if pref in {"light", "dark"} else "light"

    def _settings_page(
        self,
        values: dict[str, object],
        errors: dict[str, str],
        notice: str = "",
        *,
        theme: str | None = None,
    ) -> None:
        """``theme``: 해 · 달 버튼(?theme=)으로 고른 값 — 화면에 쓰고 쿠키로 기억한다."""
        assert self.server.root is not None
        body = render_settings(
            values,
            token=self.server.settings_token,
            errors=errors,
            notice=notice,
            log=user_settings.read_log(self.server.root),
        )
        self._send(
            200 if not errors else 400,
            orbit_layout(body, theme or self._theme(), "settings"),
            theme=theme,
        )

    def _settings_post(self) -> None:
        """설정 화면 두 단계: review(확인 화면) → save(저장). 둘 다 값을 다시 검사한다."""
        root = self.server.root
        if root is None:
            self._send(404, "<!doctype html><meta charset='utf-8'><p>없는 화면</p>")
            return
        if not self.server.same_origin(self.headers.get("Origin"), self.headers.get("Host")):
            self._send(403, "<!doctype html><meta charset='utf-8'><p>다른 출처의 요청</p>")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= 32_768:
            self._send(400, "<!doctype html><meta charset='utf-8'><p>잘못된 요청</p>")
            return
        raw = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        form = {k: v[-1] for k, v in raw.items()}
        if not secrets.compare_digest(form.get("token", ""), self.server.settings_token):
            self._send(403, "<!doctype html><meta charset='utf-8'><p>화면을 새로 고친 뒤 다시</p>")
            return
        current = user_settings.load(root)
        universe = read_universe(root / "data" / "toss" / "universe.json")
        new, errors = user_settings.validate(form, current, universe)
        if errors:
            self._settings_page(new, errors)
            return
        changes = user_settings.diff(current, new)
        if self.path == "/settings/review":
            body = render_review(changes, form, token=self.server.settings_token)
            self._send(200, orbit_layout(body, self._theme(), "settings"))
            return
        user_settings.apply(root, changes, datetime.now(UTC), new=new)
        self.send_response(303)
        self.send_header("Location", f"/settings?saved={len(changes)}")
        self.send_header("Content-Length", "0")
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def do_POST(self) -> None:
        """놓친 작업 다시 실행 버튼 · 설정 화면(확인 → 저장)만 받는다."""
        if self.path in ("/settings/review", "/settings/save"):
            self._settings_post()
            return
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

    def _send(
        self,
        status: int,
        html: str,
        *,
        theme: str | None = None,
        cookies: list[str] | None = None,
        csp: str | None = None,
    ) -> None:
        self._send_bytes(
            status,
            html.encode("utf-8"),
            "text/html; charset=utf-8",
            theme=theme,
            cookies=cookies,
            csp=csp,
        )

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        theme: str | None = None,
        cookies: list[str] | None = None,
        csp: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        jar = ([f"dashboard_theme={theme}"] if theme in {"light", "dark"} else []) + list(
            cookies or []
        )
        for pair in jar:
            self.send_header(
                "Set-Cookie", f"{pair}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict"
            )
        for name, value in _HEADERS.items():
            if name == "Content-Security-Policy" and csp:
                value = csp
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

    return DashboardServer((host, port), loader, catchup or CatchupRunner(root), root)
