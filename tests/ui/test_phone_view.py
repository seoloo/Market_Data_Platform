"""휴대폰으로 보기 — Tailscale 너머 요청은 보기만, 다른 이름은 막기, 홈 화면 앱 정보, 연결 버튼."""

from __future__ import annotations

import http.client
import json
import re
import threading
import urllib.parse
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest

from auto_trading.ops import phone_link, task_setup
from auto_trading.ops.phone_link import PhoneState
from auto_trading.ui.server import DashboardServer, make_server
from auto_trading.ui.settings_extra import phone_tab

TS = "my-pc.tail1234.ts.net"
STATUS = {
    "BackendState": "Running",
    "Self": {"DNSName": TS + "."},
    "Peer": {
        "a": {"HostName": "iPhone", "OS": "iOS", "Online": True},
        "b": {"HostName": "laptop", "OS": "windows", "Online": True},
        "c": {"HostName": "old-phone", "OS": "android", "Online": False},
    },
}


class FakeTS:
    def __init__(self, serving: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.serving = serving

    def __call__(self, args: Sequence[str]) -> str:
        self.calls.append(list(args))
        if list(args) == ["status", "--json"]:
            return json.dumps(STATUS)
        if list(args) == ["serve", "status", "--json"]:
            web = {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8790"}}}
            return json.dumps({"Web": {f"{TS}:443": web}} if self.serving else {})
        return ""


def test_state_reads_host_phones_and_serve() -> None:
    st = phone_link.state(FakeTS(serving=True))
    assert st.running and st.host == TS and st.serving
    assert st.phones == ("iPhone",)
    assert st.url == f"https://{TS}/live"
    assert not phone_link.state(FakeTS()).serving
    assert phone_link.state(installed=False) == PhoneState(False)


def test_serve_on_off_args() -> None:
    ts = FakeTS()
    phone_link.serve_on(ts)
    phone_link.serve_off(ts)
    assert ts.calls == [
        ["serve", "--bg", "--https=443", "http://127.0.0.1:8790"],
        ["serve", "--https=443", "off"],
    ]


def test_phone_tab_steps() -> None:
    html, forms = phone_tab(phone_link.state(FakeTS(serving=True)), None, token="T")
    assert f"https://{TS}/live" in html and "홈 화면에 추가" in html and "휴대폰 1대" in html
    assert 'action="/settings/phone"' in forms
    off, _ = phone_tab(PhoneState(False), None, token="T")
    assert "설치 안 됨" in off and " disabled" in off


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[DashboardServer, int]]:
    server = make_server(tmp_path, port=0, catchup=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _req(
    port: int, method: str, path: str, headers: dict[str, str], body: str = ""
) -> tuple[int, str, dict[str, str]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body.encode() or None, headers=headers)
    r = conn.getresponse()
    out = r.status, r.read().decode("utf-8", "replace"), dict(r.getheaders())
    conn.close()
    return out


def test_host_guard_and_remote_read_only(served: tuple[DashboardServer, int]) -> None:
    server, port = served
    local = {"Host": f"127.0.0.1:{port}"}
    phone = {"Host": TS, "X-Forwarded-For": "100.64.0.2", "Tailscale-User-Login": "me@x"}
    assert _req(port, "GET", "/settings", {"Host": "evil.example:80"})[0] == 403
    assert _req(port, "GET", "/settings", {"Host": "ts.net"})[0] == 403
    code, page, _ = _req(port, "GET", "/settings", phone)
    assert code == 200 and "휴대폰에서는 보기만" in page and 'type="password"' not in page
    assert 'name="token"' not in page  # 토큰도 휴대폰에 안 보낸다
    code, local_page, _ = _req(port, "GET", "/settings", local)
    assert code == 200 and 'type="password"' in local_page
    token = re.search(r'name="token" value="([^"]+)"', local_page)
    assert token
    form = urllib.parse.urlencode({"token": token.group(1), "action": "on"})
    post_headers = {
        **phone,
        "Origin": f"https://{TS}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    for path in ("/settings/halt", "/settings/keys", "/settings/tasks", "/catchup"):
        assert _req(port, "POST", path, post_headers, form)[0] == 403, path
    assert server.root is not None
    assert not (server.root / "data" / "HALT").exists()


def test_manifest_and_icons(served: tuple[DashboardServer, int]) -> None:
    _, port = served
    host = {"Host": TS}
    code, body, headers = _req(port, "GET", "/manifest.webmanifest", host)
    data = json.loads(body)
    assert code == 200 and data["start_url"] == "/live" and data["display"] == "standalone"
    assert headers["Content-Type"].startswith("application/manifest+json")
    for path in ("/apple-touch-icon.png", "/assets/orbit-app-192.png", "/assets/orbit-app-512.png"):
        assert _req(port, "GET", path, host)[0] == 200, path
    code, page, headers = _req(port, "GET", "/settings", {"Host": f"127.0.0.1:{port}"})
    assert 'rel="manifest"' in page and 'rel="apple-touch-icon"' in page
    assert "manifest-src 'self'" in headers["Content-Security-Policy"]


def test_phone_route_registers_server_and_serves(
    served: tuple[DashboardServer, int], tmp_path: Path
) -> None:
    server, port = served
    tdir = tmp_path / task_setup.TEMPLATE_DIR
    tdir.mkdir(parents=True)
    (tdir / "AutoTrading_Dashboard.xml").write_text("<T>{REPO}{USER}</T>", encoding="utf-8")
    ps: list[str] = []

    def runner(script: str) -> str:
        ps.append(script)
        return "[]" if "Get-ScheduledTask -TaskName 'AutoTrading_*'" in script else ""

    ts = FakeTS()
    server.task_runner = runner
    server.phone_runner = ts
    local = {"Host": f"127.0.0.1:{port}"}
    page = _req(port, "GET", "/settings", local)[1]
    token = re.search(r'name="token" value="([^"]+)"', page)
    assert token
    headers = {
        **local,
        "Origin": f"http://127.0.0.1:{port}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    form = urllib.parse.urlencode({"token": token.group(1), "action": "on"})
    assert _req(port, "POST", "/settings/phone", headers, form)[0] == 303
    assert any("Register-ScheduledTask -TaskName 'AutoTrading_Dashboard'" in c for c in ps)
    assert any("Start-ScheduledTask -TaskName 'AutoTrading_Dashboard'" in c for c in ps)
    assert ["serve", "--bg", "--https=443", "http://127.0.0.1:8790"] in ts.calls
    assert "켰어요" in server.phone_note[0]
    form = urllib.parse.urlencode({"token": token.group(1), "action": "off"})
    assert _req(port, "POST", "/settings/phone", headers, form)[0] == 303
    assert ts.calls[-1] == ["serve", "--https=443", "off"]


def test_phone_error_link_is_clickable_only_for_tailscale() -> None:
    err = "연결 실패: visit https://login.tailscale.com/f/serve?node=abc then <b>x</b> https://evil.example/"
    html, _ = phone_tab(PhoneState(True, True, TS), None, token="T", error=err)
    assert '<a href="https://login.tailscale.com/f/serve?node=abc"' in html
    assert 'href="https://evil.example/' not in html and "&lt;b&gt;x" in html
