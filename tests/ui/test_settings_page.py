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
    assert v["fire.target_krw"] == 100_000_000 and v["glide.monthly_krw"] == 300_000
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
    _, err = us.validate(_form(v, **{"glide.monthly_krw": "6,000,000"}), v)
    assert "glide.monthly_krw" in err
    form = _form(v)
    form.pop("acc.QS.on")  # 스위치를 끄면 칸이 안 온다
    new, err = us.validate(form, v)
    assert not err and new["acc.QS.on"] is False


def test_apply_logs_halt_and_acc_history(tmp_path: Path) -> None:
    v = us.load(tmp_path)
    form = _form(v, **{"acc.WQTM.usd": "2"})
    form.pop("acc.QS.on")
    form["safety.halt"] = "on"
    new, _ = us.validate(form, v)
    changes = us.diff(v, new)
    assert {c.key for c in changes} == {"acc.WQTM.usd", "acc.QS.on", "safety.halt"}
    us.apply(tmp_path, changes, NOW)
    assert (tmp_path / "data" / "HALT").exists()
    assert us.load(tmp_path)["safety.halt"] is True
    assert us.acc_history(tmp_path, "WQTM") == [("2026-10-11", 2.0)]  # 내일부터
    assert us.acc_history(tmp_path, "QS") == [("2026-10-11", 0.0)]  # 끈 기간은 0
    log = us.read_log(tmp_path)
    assert {r["what"] for r in log} >= {"WQTM 하루 금액 $1 → $2", "비상 정지 꺼짐 → 켜짐"}
    # 다시 켜면 파일을 지운다
    v2 = us.load(tmp_path)
    form2 = _form(v2)
    form2.pop("safety.halt")
    new2, _ = us.validate(form2, v2)
    us.apply(tmp_path, us.diff(v2, new2), NOW)
    assert not (tmp_path / "data" / "HALT").exists()


def test_toss_plans_follow_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.toss_accumulate as mod

    monkeypatch.setattr(mod, "REPO", tmp_path)
    before = {p.symbol: p for p in mod._plans()}
    v = us.load(tmp_path)
    new, _ = us.validate(_form(v, **{"acc.BTQ.usd": "3", "glide.monthly_krw": "500,000"}), v)
    us.apply(tmp_path, us.diff(v, new), NOW)
    plan = next(p for p in mod._plans() if p.symbol == "BTQ")
    assert plan.rate_on("2026-10-10") == before["BTQ"].rate_on("2026-10-10")  # 오늘은 그대로
    assert plan.rate_on("2026-10-11") == 3.0
    assert mod._m3_monthly() == 500_000.0


def test_pages_render_without_scripts() -> None:
    v = us.load(Path("__nope__"))
    html = render_settings(v, token="T", errors={"fire.target_krw": "숫자로"})
    assert '<form method="post" action="/settings/review">' in html
    assert 'name="token" value="T"' in html and "<script" not in html
    assert "🔒 Glide 규칙 · 잠김" in html and "준비 중" in html
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
