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
* **예외 셋째** (사용자 2026-10-11 '전부 정리'): ``POST /settings/liquidate`` — 확인 창을 거쳐
  정리 실행기(``python -m scripts.liquidate start <범위> 오르빗``)를 따로 띄운다. 서버는 키를 읽거나
  주문하지 않는다. ``POST /settings/unhalt`` 는 그룹 정지 파일(``data/HALT_<그룹>``)만 지운다.
  둘 다 설정과 같은 토큰 · 같은 출처 검사, 휴대폰에서는 안 받는다.
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

import json
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from auto_trading.ops import env_keys, phone_link, task_setup, user_settings
from auto_trading.ops.conn_test import run_test
from auto_trading.ops.kill_switch import GROUPS, group_path, halted_reason
from auto_trading.ops.kr_accumulate import read_universe
from auto_trading.ops.liquidate import SCOPES, Store, job_text
from auto_trading.ui.catchup import CatchupRunner
from auto_trading.ui.data import DashboardData, load_dashboard
from auto_trading.ui.liquidate_panel import liquidate_panel
from auto_trading.ui.orbit_home import layout as orbit_layout
from auto_trading.ui.orbit_icons import ICON_ASSETS
from auto_trading.ui.render import PAGES, render_page
from auto_trading.ui.settings_extra import keys_tab, phone_tab, tasks_tab
from auto_trading.ui.settings_page import render_remote, render_review, render_settings

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
    # 아이폰 '홈 화면에 추가' 아이콘 (Safari 는 /apple-touch-icon.png 도 찾아본다)
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/assets/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/assets/orbit-app-192.png": ("orbit-app-192.png", "image/png"),
    "/assets/orbit-app-512.png": ("orbit-app-512.png", "image/png"),
}
_MANIFEST = json.dumps(
    {
        "name": "오르빗",
        "short_name": "오르빗",
        "start_url": "/live",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0c1630",
        "theme_color": "#0c1630",
        "lang": "ko",
        "icons": [
            {"src": "/assets/orbit-app-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/assets/orbit-app-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    },
    ensure_ascii=False,
).encode("utf-8")
"""휴대폰 홈 화면 앱(PWA) 정보 — 오프라인 저장 없이 열 때마다 PC 에서 새로 읽는다."""
_TS_SUFFIX = ".ts.net"
"""휴대폰은 Tailscale 이름(``<PC>.<tailnet>.ts.net``)으로만 들어온다 (ops/phone_link.py)."""
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
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; manifest-src 'self'; "
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
        self.key_tests: dict[str, tuple[bool, str]] = {}
        """설정 '연결 시험' 마지막 결과 (키 · 금액 없는 한 줄)."""
        self.task_note: tuple[str, str] = ("", "")
        """설정 '자동 실행' 마지막 결과 (알림, 오류)."""
        self.task_runner: task_setup.Runner | None = None
        """시험에서 PowerShell 대신 끼운다."""
        self._task_cache: tuple[float, dict[str, task_setup.TaskState] | None] = (0.0, None)
        self.phone_runner: phone_link.Runner | None = None
        """시험에서 tailscale 대신 끼운다."""
        self.phone_note: tuple[str, str] = ("", "")
        self.liq_runner: Callable[[list[str]], None] = _spawn
        """'전부 정리' 실행기를 띄운다 (시험에서 바꿔 끼운다)."""
        super().__init__(address, _Handler)

    def task_states(self, *, fresh: bool = False) -> dict[str, task_setup.TaskState] | None:
        """이 PC 의 예약 상태 (20 초 저장). 못 읽으면 None."""
        at, cached = self._task_cache
        if not fresh and cached is not None and time.monotonic() - at < 20:
            return cached
        try:
            got = task_setup.status(self.task_runner or task_setup._powershell)
        except Exception:
            return None
        self._task_cache = (time.monotonic(), got)
        return got

    def host_ok(self, host: str | None) -> bool:
        """이 PC(127.0.0.1 · localhost) 또는 Tailscale 이름만 — 다른 이름은 DNS 바꿔치기로 본다."""
        name = (host or "").rsplit(":", 1)[0].lower()
        return name in ALLOWED_HOSTS or (name.endswith(_TS_SUFFIX) and len(name) > len(_TS_SUFFIX))

    def same_origin(self, origin: str | None, host: str | None) -> bool:
        port = self.server_address[1]
        allowed = {f"{h}:{port}" for h in ALLOWED_HOSTS}
        return host in allowed and origin in {f"http://{a}" for a in allowed}


class _Handler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "AutoTradingDashboard"
    sys_version = ""

    def _remote(self) -> bool:
        """휴대폰(Tailscale serve 너머)에서 온 요청 — 보기만 된다."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        return (
            host not in ALLOWED_HOSTS
            or self.headers.get("X-Forwarded-For") is not None
            or self.headers.get("Tailscale-User-Login") is not None
        )

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self.server.host_ok(self.headers.get("Host")):
            self._send(403, "<!doctype html><meta charset='utf-8'><p>이 주소로는 열 수 없어요</p>")
            return
        if path == "/manifest.webmanifest":
            self._send_bytes(200, _MANIFEST, "application/manifest+json")
            return
        if path == "/theme":  # 오르빗 테마 버튼이 화면을 다시 불러오지 않고 기억만 남긴다
            pick = (parse_qs(self.path.partition("?")[2]).get("set") or [""])[0]
            if pick not in {"light", "dark"}:
                self._send(400, "<!doctype html><meta charset='utf-8'><p>light · dark 만</p>")
                return
            self._send_bytes(204, b"", "text/plain; charset=utf-8", theme=pick)
            return
        if path == "/settings" and self.server.root is not None and self._remote():
            self._send(200, orbit_layout(render_remote(), self._theme(), "settings"))
            return
        if path == "/settings" and self.server.root is not None:
            query = parse_qs(self.path.partition("?")[2])
            saved_n = (query.get("saved") or [""])[0]
            notice = f"저장했어요 · 바뀐 값 {saved_n}개" if saved_n.isdigit() else ""
            if (query.get("liq") or [""])[0] == "1":
                notice = "정리를 시작했어요 — 결과는 텔레그램으로 와요 (잠시 뒤 새로 고침)"
            if (query.get("unhalt") or [""])[0] == "1":
                notice = "그룹 정지를 풀었어요 — 다음 실행부터 그 전략이 자기 규칙대로 다시 사요"
            keys_n = (query.get("keys") or [""])[0]
            if keys_n.isdigit():
                notice = f"키 {keys_n}개를 .env 에 저장했어요 (화면에는 다시 보이지 않아요)"
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
        key_errors: dict[str, str] | None = None,
    ) -> None:
        """``theme``: 해 · 달 버튼(?theme=)으로 고른 값 — 화면에 쓰고 쿠키로 기억한다."""
        assert self.server.root is not None
        root = self.server.root
        status = env_keys.key_status(root)
        states = self.server.task_states()
        defaults = _default_groups(status, states)
        tnote, terr = self.server.task_note
        body = render_settings(
            values,
            token=self.server.settings_token,
            errors=errors,
            notice=notice,
            log=user_settings.read_log(root),
            halt_note=_halt_note(root),
            liquidate=_liq_panel(root, self.server.settings_token),
            extra_tabs={
                "keys": keys_tab(
                    status,
                    token=self.server.settings_token,
                    errors=key_errors,
                    tests=self.server.key_tests,
                ),
                "phone": phone_tab(
                    phone_link.state(self.server.phone_runner),
                    (states or {}).get("AutoTrading_Dashboard"),
                    token=self.server.settings_token,
                    note=self.server.phone_note[0],
                    error=self.server.phone_note[1],
                ),
                "tasks": tasks_tab(
                    states,
                    token=self.server.settings_token,
                    defaults=defaults,
                    note=tnote,
                    error=terr,
                ),
            },
        )
        self._send(
            200 if not (errors or key_errors) else 400,
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
        if self.path == "/settings/keys":  # 값은 .env 로만 · 화면 · 기록에 남기지 않는다
            updates, key_errors = env_keys.validate(form)
            if env_keys.TOSS_SWITCH in updates:
                now_on = bool(env_keys.key_status(root)[env_keys.TOSS_SWITCH]["set"])
                if (updates[env_keys.TOSS_SWITCH] == "true") == now_on:
                    updates.pop(env_keys.TOSS_SWITCH)  # 바뀌지 않았으면 쓰지 않는다
            if key_errors:
                self._settings_page(user_settings.load(root), {}, key_errors=key_errors)
                return
            saved = env_keys.save_keys(root, updates, datetime.now(UTC))
            self._redirect(f"/settings?keys={len(saved)}#set-keys")
            return
        if self.path == "/settings/test":
            service = form.get("service", "")
            self.server.key_tests[service] = run_test(root, service)
            self._redirect("/settings#set-keys")
            return
        if self.path == "/settings/phone":
            prun = self.server.phone_runner
            try:
                if form.get("action") == "on":
                    trun = self.server.task_runner or task_setup._powershell
                    now = self.server.task_states(fresh=True)
                    if now is None:
                        raise RuntimeError("이 PC 의 Windows 예약을 읽지 못했어요")
                    made, _ = task_setup.register_missing(root, ["phone"], now, trun)
                    srv = now.get("AutoTrading_Dashboard")
                    if made or srv is None or srv.state != "Running":
                        task_setup.start("AutoTrading_Dashboard", trun)
                    phone_link.serve_on(prun)
                    self.server.phone_note = (
                        "휴대폰 연결을 켰어요 — 아래 주소를 아이폰에서 열어요",
                        "",
                    )
                elif form.get("action") == "off":
                    phone_link.serve_off(prun)
                    self.server.phone_note = (
                        "휴대폰 연결을 껐어요 (화면 서버는 이 PC 안에서만 계속)",
                        "",
                    )
            except Exception as exc:  # tailscale 안내 문구(HTTPS 허용 링크 등)를 그대로 보여 준다
                self.server.phone_note = ("", f"연결 실패: {str(exc)[:300]}")
            self.server.task_states(fresh=True)
            self._redirect("/settings#set-phone")
            return
        if self.path == "/settings/tasks":
            groups = [g for g, _, _ in task_setup.GROUPS if form.get(f"g_{g}") == "on"]
            action = form.get("action")
            states = self.server.task_states(fresh=True)
            runner = self.server.task_runner or task_setup._powershell
            if not groups:
                self.server.task_note = ("", "묶음을 하나 이상 골라 주세요")
            elif states is None:
                self.server.task_note = ("", "이 PC 의 Windows 예약을 읽지 못했어요")
            else:
                try:
                    if action == "on":
                        made, enabled = task_setup.register_missing(root, groups, states, runner)
                        self.server.task_note = (
                            f"자동 실행 켬 · 새로 만든 예약 {len(made)}개 · 다시 켠 예약 "
                            f"{len(enabled)}개 (이미 있던 예약은 그대로)",
                            "",
                        )
                    elif action == "off":
                        off = task_setup.disable(groups, states, runner)
                        self.server.task_note = (f"자동 실행 끔 · 예약 {len(off)}개 사용 안 함", "")
                    else:
                        self.server.task_note = ("", "알 수 없는 요청")
                except Exception as exc:  # PowerShell 오류 문구에 비밀값은 없다(예약 이름 · 경로)
                    self.server.task_note = ("", f"예약 등록 실패: {str(exc)[:200]}")
                self.server.task_states(fresh=True)
            self._redirect("/settings#set-tasks")
            return
        if self.path == "/settings/liquidate":  # '정말 … 정리할까요?' 창에서 이미 확인함
            scope = form.get("scope", "")
            if scope not in SCOPES:
                self._send(400, "<!doctype html><meta charset='utf-8'><p>모르는 범위</p>")
                return
            try:
                self.server.liq_runner(["start", scope, "오르빗"])
            except OSError as exc:
                self._send(500, f"<!doctype html><meta charset='utf-8'><p>실행 실패: {exc}</p>")
                return
            self._redirect("/settings?liq=1#set-liquidate")
            return
        if self.path == "/settings/unhalt":
            group = form.get("group", "")
            if group not in GROUPS:
                self._send(400, "<!doctype html><meta charset='utf-8'><p>모르는 그룹</p>")
                return
            group_path(root / "data" / "HALT", group).unlink(missing_ok=True)
            self._redirect("/settings?unhalt=1#set-liquidate")
            return
        current = user_settings.load(root)
        if self.path == "/settings/halt":  # 비상 정지는 확인 창을 거친 이 길로만 켜고 끈다
            want = form.get("action")
            if want not in ("on", "off") or (want == "on") == bool(current["safety.halt"]):
                self._send(409, "<!doctype html><meta charset='utf-8'><p>이미 그 상태예요</p>")
                return
            new = {**current, "safety.halt": want == "on"}
            changes = user_settings.diff(current, new)
            user_settings.apply(root, changes, datetime.now(UTC), new=new)
            self._redirect(f"/settings?saved={len(changes)}#set-safety")
            return
        if self.path == "/settings/delete":  # '정말 삭제하시겠습니까?' 창에서 이미 확인함
            flag = f"{form.get('key', '')}.removed"
            if flag not in current or current[flag]:
                self._send(400, "<!doctype html><meta charset='utf-8'><p>없는 종목</p>")
                return
            new = {**current, flag: True}
            changes = user_settings.diff(current, new)
            user_settings.apply(root, changes, datetime.now(UTC), new=new)
            self._redirect(f"/settings?saved={len(changes)}#set-toss")
            return
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
        self._redirect(f"/settings?saved={len(changes)}")

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        for name, value in _HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def do_POST(self) -> None:
        """놓친 작업 다시 실행 · 설정 화면(확인 → 저장)만 받는다. 휴대폰에서는 안 받는다."""
        if self._remote():
            self._send(403, "<!doctype html><meta charset='utf-8'><p>휴대폰에서는 보기만 돼요</p>")
            return
        if self.path in (
            "/settings/review",
            "/settings/save",
            "/settings/delete",
            "/settings/halt",
            "/settings/liquidate",
            "/settings/unhalt",
            "/settings/keys",
            "/settings/test",
            "/settings/tasks",
            "/settings/phone",
        ):
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


def _liq_panel(root: Path, token: str) -> tuple[str, str]:
    halt = root / "data" / "HALT"
    halts = {g: halted_reason(group_path(halt, g)) for g in GROUPS}
    jobs = Store(root / "data" / "liquidate").jobs()
    return liquidate_panel(token, halts, job_text(jobs[-1]) if jobs else "")


def _spawn(args: list[str], root: Path | None = None) -> None:
    """정리 실행기를 창 없이 따로 띄운다 (이 서버가 죽어도 이어 돈다). 출력은 reports/liquidate/."""
    repo = root or Path(__file__).resolve().parents[3]
    py = repo / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.exists() else sys.executable
    out = repo / "reports" / "liquidate"
    out.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    with (out / "orbit_run.log").open("ab") as log:
        subprocess.Popen(
            [exe, "-m", "scripts.liquidate", *args],
            cwd=repo,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
        )


def _halt_note(root: Path) -> str:
    try:
        return (root / "data" / "HALT").read_text(encoding="utf-8").strip()[:120]
    except OSError:
        return ""


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


def _default_groups(
    keys: dict[str, dict[str, str | bool]], states: dict[str, task_setup.TaskState] | None
) -> list[str]:
    """자동 실행에서 처음 체크해 둘 묶음 — 이 PC 에 이미 켜진 예약이 있거나, 필요한 키가 있으면."""
    have = {n for n, st in (states or {}).items() if st.state != "Disabled"}
    out = []
    for g, _, _ in task_setup.GROUPS:
        names = {t.name for t in task_setup.TASKS if t.group == g}
        need = {
            "glide": ("TOSS_CLIENT_ID",),
            "toss": ("TOSS_CLIENT_ID",),
            "cruise": ("UPBIT_API_KEY",),
            "common": ("TELEGRAM_BOT_TOKEN",),
            "info": ("TELEGRAM_INFO_BOT_TOKEN",),
        }.get(g, ())
        if names & have or (need and all(keys.get(n, {}).get("set") for n in need)):
            out.append(g)
    return out
