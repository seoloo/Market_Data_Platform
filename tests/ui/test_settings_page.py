"""오르빗 설정: 값 검사 · 확인 → 저장 두 단계 · 같은 출처 + 토큰 · 기록 · 비상 정지 · 적립 기록."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_trading.ops import user_settings as us
from auto_trading.ui.server import make_server
from auto_trading.ui.settings_page import render_review, render_settings

NOW = datetime(2026, 10, 10, 11, 0, tzinfo=UTC)  # KST 20:00


def _form(values: dict[str, object], **over: str) -> dict[str, str]:
    """화면이 보내는 그대로: 켜진 스위치만 'on', 숫자는 글자."""
    out: dict[str, str] = {}
    for f in us.FIELDS:
        v = values[f.key]
        if f.kind == "bool":
            if v:
                out[f.key] = "on"
        else:
            out[f.key] = f"{v:,}" if isinstance(v, int) else str(v)
    out.update(over)
    return out


def test_defaults_are_todays_values(tmp_path: Path) -> None:
    v = us.load(tmp_path)
    assert v["fire.target_krw"] == 100_000_000 and v["deneb.monthly_krw"] == 300_000
    assert v["acc.WQTM.usd"] == 1.0 and v["acc.QS.on"] is True and v["safety.halt"] is False
    assert us.acc_history(tmp_path, "BTQ") == []


def test_validate_ranges_and_order(tmp_path: Path) -> None:
    v = us.load(tmp_path)
    new, err = us.validate(_form(v, **{"fire.target_krw": "200,000,000원"}), v)
    assert not err and new["fire.target_krw"] == 200_000_000
    _, err = us.validate(_form(v, **{"acc.BTQ.usd": "50"}), v)
    assert "acc.BTQ.usd" in err
    _, err = us.validate(_form(v, **{"fire.rate_mid": "30"}), v)
    assert "fire.rate_mid" in err  # 좋을 때 ≥ 보통
    _, err = us.validate(_form(v, **{"deneb.monthly_krw": "6,000,000"}), v)
    assert "deneb.monthly_krw" in err
    form = _form(v)
    form.pop("acc.QS.on")  # 스위치를 끄면 칸이 안 온다
    new, err = us.validate(form, v)
    assert not err and new["acc.QS.on"] is False


def test_apply_logs_and_acc_history(tmp_path: Path) -> None:
    v = us.load(tmp_path)
    form = _form(v, **{"acc.WQTM.usd": "2"})
    form.pop("acc.QS.on")
    new, _ = us.validate(form, v)
    changes = us.diff(v, new)
    assert {c.key for c in changes} == {"acc.WQTM.usd", "acc.QS.on"}
    us.apply(tmp_path, changes, NOW)
    assert us.acc_history(tmp_path, "WQTM") == [("2026-10-11", 2.0)]  # 내일부터
    assert us.acc_history(tmp_path, "QS") == [("2026-10-11", 0.0)]  # 끈 기간은 0
    log = us.read_log(tmp_path)
    assert {r["what"] for r in log} >= {"WQTM 하루 금액 $1 → $2"}


def test_big_form_never_touches_halt(tmp_path: Path) -> None:
    """비상 정지는 전용 확인 창으로만 — 켜진 채로 다른 값을 저장해도 꺼지지 않는다."""
    halt = tmp_path / "data" / "HALT"
    halt.parent.mkdir(parents=True)
    halt.write_text("시험", encoding="utf-8")
    v = us.load(tmp_path)
    form = _form(v, **{"fire.target_krw": "200,000,000"})
    form.pop("safety.halt", None)
    new, _ = us.validate(form, v)
    us.apply(tmp_path, us.diff(v, new), NOW, new=new)
    assert halt.exists()
    new2, _ = us.validate({**_form(us.load(tmp_path)), "safety.halt": "on"}, us.load(tmp_path))
    assert not any(c.key == "safety.halt" for c in us.diff(us.load(tmp_path), new2))


def test_halt_buttons_and_modal() -> None:
    off = render_settings(us.load(Path("__nope__")), token="T")
    assert "✅ 정상 운영 중" in off and "정말 비상 정지를 켜시겠습니까?" in off
    assert '<form id="halt-f" method="post" action="/settings/halt">' in off
    assert 'name="action" value="on"' in off and 'name="safety.halt"' not in off
    on = render_settings(
        {**us.load(Path("__nope__")), "safety.halt": True}, token="T", halt_note="설정 화면에서 켬"
    )
    assert "⛔ 비상 정지 중" in on and "설정 화면에서 켬" in on
    assert "정말 비상 정지를 푸시겠습니까?" in on and 'name="action" value="off"' in on


def test_toss_plans_follow_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.toss_accumulate as mod

    monkeypatch.setattr(mod, "REPO", tmp_path)
    before = {p.symbol: p for p in mod._plans()}
    v = us.load(tmp_path)
    new, _ = us.validate(_form(v, **{"acc.BTQ.usd": "3", "deneb.monthly_krw": "500,000"}), v)
    us.apply(tmp_path, us.diff(v, new), NOW)
    plan = next(p for p in mod._plans() if p.symbol == "BTQ")
    assert plan.rate_on("2026-10-10") == before["BTQ"].rate_on("2026-10-10")  # 오늘은 그대로
    assert plan.rate_on("2026-10-11") == 3.0
    assert mod._deneb_monthly() == 500_000.0


def test_pages_render_without_scripts() -> None:
    v = us.load(Path("__nope__"))
    html = render_settings(v, token="T", errors={"fire.target_krw": "숫자로"})
    assert '<form method="post" action="/settings/review">' in html
    assert 'name="token" value="T"' in html and "<script" not in html
    assert "🔒 Deneb 규칙 · 잠김" in html and "준비 중" in html
    review = render_review(
        [us.Change("fire.target_krw", "파이어 목표", 1, 2, "바로")], {"a": "1"}, token="T"
    )
    assert 'action="/settings/save"' in review and 'name="a" value="1"' in review


class TestServer:
    @pytest.fixture
    def base(self, tmp_path: Path) -> Iterator[str]:
        server = make_server(tmp_path, port=0, catchup=None)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    @staticmethod
    def _post(
        base: str, path: str, form: dict[str, str], origin: str | None = None
    ) -> tuple[int, str]:
        request = urllib.request.Request(
            base + path, data=urllib.parse.urlencode(form).encode(), method="POST"
        )
        request.add_header("Origin", origin or base)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: object, **kwargs: object) -> None:
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(request) as response:
                return int(response.status), response.read().decode()
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode()

    def test_review_then_save(self, base: str, tmp_path: Path) -> None:
        with urllib.request.urlopen(base + "/settings") as r:
            page = r.read().decode()
        token = re.search(r'name="token" value="([^"]+)"', page)
        assert token and "<script" not in page
        form = _form(us.load(tmp_path), token=token.group(1), **{"fire.target_krw": "200,000,000"})
        code, review = self._post(base, "/settings/review", form)
        assert code == 200 and "바뀐 값 1개를 저장할까요?" in review
        assert not (tmp_path / "data" / "settings" / "settings.json").exists()  # 확인만
        assert self._post(base, "/settings/save", form, origin="http://evil.example")[0] == 403
        assert self._post(base, "/settings/save", {**form, "token": "x"})[0] == 403
        code, _ = self._post(base, "/settings/save", form)
        assert code == 303
        assert us.get(tmp_path, "fire.target_krw") == 200_000_000
        with urllib.request.urlopen(base + "/settings?saved=1") as r:
            after = r.read().decode()
        assert (
            "저장했어요 · 바뀐 값 1개" in after
            and "파이어 목표 100,000,000원 → 200,000,000원" in after
        )
        saved = json.loads((tmp_path / "data" / "settings" / "settings.json").read_text("utf-8"))
        assert saved["values"]["fire.target_krw"] == 200_000_000

    def test_bad_value_shows_error_and_saves_nothing(self, base: str, tmp_path: Path) -> None:
        with urllib.request.urlopen(base + "/settings") as r:
            token = re.search(r'name="token" value="([^"]+)"', r.read().decode())
        assert token
        form = _form(us.load(tmp_path), token=token.group(1), **{"acc.BTQ.usd": "abc"})
        code, page = self._post(base, "/settings/save", form)
        assert code == 400 and "숫자로 적어 주세요" in page
        assert not (tmp_path / "data" / "settings").exists()


def test_theme_button_on_settings_page(tmp_path: Path) -> None:
    server = make_server(tmp_path, port=0, catchup=None)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/settings?theme=dark") as r:
            html = r.read().decode()
            cookie = r.headers.get("Set-Cookie", "")
        assert 'data-theme="dark"' in html and "dashboard_theme=dark" in cookie
        assert 'href="/settings?theme=light"' in html
    finally:
        server.shutdown()
        server.server_close()


def test_side_menu_is_sticky() -> None:
    html = render_settings(us.load(Path("__nope__")), token="T")
    assert "position:sticky;top:16px" in html


def test_halt_route_on_then_off(tmp_path: Path) -> None:
    server = make_server(tmp_path, port=0, catchup=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/settings") as r:
            token = re.search(r'name="token" value="([^"]+)"', r.read().decode())
        assert token
        halt = tmp_path / "data" / "HALT"
        post = TestServer._post
        assert post(base, "/settings/halt", {"token": token.group(1), "action": "on"})[0] == 303
        assert halt.exists()
        assert post(base, "/settings/halt", {"token": token.group(1), "action": "on"})[0] == 409
        assert post(base, "/settings/halt", {"token": "x", "action": "off"})[0] == 403
        assert post(base, "/settings/halt", {"token": token.group(1), "action": "off"})[0] == 303
        assert not halt.exists()
        log = [r["what"] for r in us.read_log(tmp_path)]
        assert log[:2] == ["비상 정지 켜짐 → 꺼짐", "비상 정지 꺼짐 → 켜짐"]
    finally:
        server.shutdown()
        server.server_close()


def test_theme_route_sets_cookie_only(tmp_path: Path) -> None:
    server = make_server(tmp_path, port=0, catchup=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/theme?set=dark") as r:
            assert r.status == 204 and "dashboard_theme=dark" in r.headers.get("Set-Cookie", "")
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/theme?set=evil")
    finally:
        server.shutdown()
        server.server_close()


def _serve(tmp_path: Path) -> tuple[object, str, str]:
    server = make_server(tmp_path, port=0, catchup=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    with urllib.request.urlopen(base + "/settings") as r:
        token = re.search(r'name="token" value="([^"]+)"', r.read().decode())
    assert token
    return server, base, token.group(1)


def test_settings_tabs_one_page_per_menu() -> None:
    html = render_settings(us.load(Path("__nope__")), token="T")
    assert ".set-tab:target{display:flex}" in html
    assert html.count('class="set-tab"') == 7  # 키 · 예약 쪽은 서버가 붙인다
    assert "html[data-page=settings] .topbar{position:sticky" in html


def test_keys_route_writes_env_never_echoes(tmp_path: Path) -> None:
    secret = "fake-toss-id-for-test"
    server, base, token = _serve(tmp_path)
    post = TestServer._post
    try:
        code, body = post(base, "/settings/keys", {"token": token, "TOSS_CLIENT_ID": secret})
        assert code == 303
        assert f"TOSS_CLIENT_ID={secret}" in (tmp_path / ".env").read_text(encoding="utf-8")
        with urllib.request.urlopen(base + "/settings?keys=1") as r:
            page = r.read().decode()
        assert secret not in page and "키 1개를 .env 에 저장했어요" in page
        bad = "bad value!!"
        code, body = post(base, "/settings/keys", {"token": token, "UPBIT_API_KEY": bad})
        assert code == 400 and bad not in body and "형식이 맞지 않아요" in body
        assert post(base, "/settings/keys", {"token": "x", "TOSS_CLIENT_ID": secret})[0] == 403
        assert secret not in (tmp_path / "data" / "settings" / "changes.jsonl").read_text(
            encoding="utf-8"
        )
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_tasks_route_uses_runner_and_never_overwrites(tmp_path: Path) -> None:
    from auto_trading.ops import task_setup

    tdir = tmp_path / task_setup.TEMPLATE_DIR
    tdir.mkdir(parents=True)
    for t in task_setup.TASKS:
        (tdir / f"{t.name}.xml").write_text("<Task>{REPO}</Task>", encoding="utf-8")
    calls: list[str] = []
    row = {"n": "AutoTrading_Upbit_T2_Live", "s": "Ready", "nx": "", "lr": "", "res": 0}
    live = [json.dumps([row])]

    def runner(script: str) -> str:
        calls.append(script)
        return live[0] if "Get-ScheduledTask -TaskName 'AutoTrading_*'" in script else ""

    server, base, token = _serve(tmp_path)
    server.task_runner = runner  # type: ignore[attr-defined]
    post = TestServer._post
    try:
        assert post(base, "/settings/tasks", {"token": token, "action": "on"})[0] == 303
        assert "하나 이상" in server.task_note[1]  # type: ignore[attr-defined]
        form = {"token": token, "g_vega": "on", "action": "on"}
        assert post(base, "/settings/tasks", form)[0] == 303
        reg = [c for c in calls if "Register-ScheduledTask" in c]
        assert len(reg) == 1 and "AutoTrading_Vega" in reg[0]
        assert "새로 만든 예약 1개" in server.task_note[0]  # type: ignore[attr-defined]
        made = {"n": "AutoTrading_Vega", "s": "Ready", "nx": "", "lr": "", "res": 0}
        live[0] = json.dumps([row, made])
        assert post(base, "/settings/tasks", {**form, "action": "off"})[0] == 303
        off = "Disable-ScheduledTask -TaskName 'AutoTrading_Vega'"
        assert any(off in c for c in calls)
        with urllib.request.urlopen(base + "/settings") as r:
            page = r.read().decode()
        assert "자동 실행" in page and "Vega 매매" in page
    finally:
        server.shutdown()  # type: ignore[attr-defined]
        server.server_close()  # type: ignore[attr-defined]


def test_liquidate_panel_buttons_confirm_and_routes(tmp_path: Path) -> None:
    """'전부 정리': 확인 창 · 토큰 · 범위 검사 · 실행기만 띄움 · 그룹 정지 풀기 · 휴대폰 거부."""
    server = make_server(tmp_path, port=0, catchup=None)
    calls: list[list[str]] = []
    server.liq_runner = calls.append
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/settings") as r:
            page = r.read().decode()
        token = re.search(r'name="token" value="([^"]+)"', page)
        assert token and 'id="set-liquidate"' in page and "그룹 정지 없음" in page
        assert (
            page.count('action="/settings/liquidate"') == 7
        )  # 전부 · Vega · Deneb · 적립 · 개별주 · Altair · Sirius
        assert "정말 Vega(업비트 BTC · ETH · SOL)를 정리할까요?" in page
        post = TestServer._post
        tok = token.group(1)
        assert post(base, "/settings/liquidate", {"token": "x", "scope": "all"})[0] == 403
        assert post(base, "/settings/liquidate", {"token": tok, "scope": "nope"})[0] == 400
        assert post(base, "/settings/liquidate", {"token": tok, "scope": "vega"})[0] == 303
        assert calls == [["start", "vega", "오르빗"]]
        halt = tmp_path / "data" / "HALT_vega"
        halt.parent.mkdir(parents=True, exist_ok=True)
        halt.write_text("전부 정리(Vega 만) · 오르빗\n", encoding="utf-8")
        with urllib.request.urlopen(base + "/settings?liq=1") as r:
            after = r.read().decode()
        assert "정리를 시작했어요" in after and "Vega 정지 중" in after
        assert 'action="/settings/unhalt"' in after
        assert post(base, "/settings/unhalt", {"token": tok, "group": "bad"})[0] == 400
        assert post(base, "/settings/unhalt", {"token": tok, "group": "vega"})[0] == 303
        assert not halt.exists()
        request = urllib.request.Request(
            base + "/settings/liquidate",
            data=urllib.parse.urlencode({"token": tok, "scope": "all"}).encode(),
            method="POST",
            headers={"Origin": base, "X-Forwarded-For": "100.64.0.2"},
        )
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(request)
        assert err.value.code == 403 and calls == [["start", "vega", "오르빗"]]
    finally:
        server.shutdown()
        server.server_close()
