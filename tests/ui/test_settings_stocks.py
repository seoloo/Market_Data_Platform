"""토스 적립 종목 추가 · 빼기 (2026-10-10): 찾기 · 상한 · 기록 · 실행기 연결 · 화면."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from auto_trading.ops import user_settings as us
from auto_trading.ops.kr_accumulate import Listed
from auto_trading.ui.settings_page import render_settings

NOW = datetime(2026, 10, 10, 11, 0, tzinfo=UTC)  # KST 20:00 → 내일 10-11
UNIVERSE = [
    Listed("005930", "삼성전자", "KOSPI", "STOCK"),
    Listed("069500", "KODEX 200", "KOSPI", "ETF"),
    Listed("NVDA", "엔비디아", "NASDAQ", "STOCK"),
    Listed("BTQ", "BTQ", "NASDAQ", "STOCK"),
]


def _form(v: dict[str, object], **over: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for f in us.fields(v["_extras"]):  # type: ignore[arg-type]
        val = v[f.key]
        if f.kind == "bool":
            if val:
                out[f.key] = "on"
        else:
            out[f.key] = str(val)
    out.update(over)
    return out


def _save(root: Path, **over: str) -> list[us.Change]:
    v = us.load(root)
    new, err = us.validate(_form(v, **over), v, UNIVERSE)
    assert not err, err
    changes = us.diff(v, new)
    us.apply(root, changes, NOW, new=new)
    return changes


def test_add_kr_and_us_then_remove(tmp_path: Path) -> None:
    ch = _save(tmp_path, **{"new.mkt": "KR", "new.query": "삼성 전자", "new.daily": "5,000"})
    assert [c.label for c in ch] == ["국내 삼성전자(005930) 추가 · 하루 ₩5,000"]
    assert us.acc_history(tmp_path, "005930") == [("2026-10-11", 5000.0)]
    ch = _save(tmp_path, **{"new.mkt": "US", "new.query": "엔비디아", "new.daily": "2"})
    assert ch[0].label == "미국 엔비디아(NVDA) 추가 · 하루 $2"
    v = us.load(tmp_path)
    assert v["x.KR.005930.daily"] == 5000 and v["x.US.NVDA.on"] is True
    assert us.active_count(v) == 5
    # 빼기 → 내일부터 0, 목록에서 사라짐, 기록 남음
    _save(tmp_path, **{"x.KR.005930.removed": "on"})
    assert us.acc_history(tmp_path, "005930")[-1] == ("2026-10-11", 0.0)
    v = us.load(tmp_path)
    assert "x.KR.005930.daily" not in v
    assert any(x["sym"] == "005930" and x.get("removed") for x in us.extras(tmp_path))
    log = [r["what"] for r in us.read_log(tmp_path)]
    assert "삼성전자 (005930) 목록에서 빼기 목록에 있음 → 뺌" in log


def test_add_errors(tmp_path: Path) -> None:
    v = us.load(tmp_path)
    _, err = us.validate(_form(v, **{"new.mkt": "KR", "new.query": "없는회사"}), v, UNIVERSE)
    assert "찾지 못했어요" in err["new.query"]
    _, err = us.validate(_form(v, **{"new.mkt": "US", "new.query": "BTQ"}), v, UNIVERSE)
    assert "이미 목록에" in err["new.query"]
    _, err = us.validate(
        _form(v, **{"new.mkt": "KR", "new.query": "KODEX 200", "new.daily": "100"}), v, UNIVERSE
    )
    assert "₩1,000 ~ ₩30,000" in err["new.daily"]
    _, err = us.validate(_form(v, **{"new.mkt": "KR", "new.query": "삼성전자"}), v, [])
    assert "목록을 아직" in err["new.query"]


def test_max_items(tmp_path: Path) -> None:
    uni = [Listed(f"00000{i}", f"회사{i}", "KOSPI", "STOCK") for i in range(6)]
    for i in range(5):
        v = us.load(tmp_path)
        new, err = us.validate(_form(v, **{"new.mkt": "KR", "new.query": f"회사{i}"}), v, uni)
        assert not err
        us.apply(tmp_path, us.diff(v, new), NOW, new=new)
    v = us.load(tmp_path)
    assert us.active_count(v) == 8
    _, err = us.validate(_form(v, **{"new.mkt": "KR", "new.query": "회사5"}), v, uni)
    assert "8 개까지" in err["new.query"]


def test_remove_builtin_and_readd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import scripts.toss_accumulate as mod

    monkeypatch.setattr(mod, "REPO", tmp_path)
    _save(tmp_path, **{"acc.QS.removed": "on"})
    _save(tmp_path, **{"new.mkt": "US", "new.query": "NVDA", "new.daily": "3"})
    syms = {p.symbol: p for p in mod._plans()}
    assert "QS" not in syms and "NVDA" in syms
    nv = syms["NVDA"]
    assert nv.rate_on("2026-10-10") == 0.0 and nv.rate_on("2026-10-11") == 3.0
    assert nv.order_exchange == "NASD" and nv.max_limit_usd == mod.EXTRA_MAX_LIMIT_USD
    v = us.load(tmp_path)
    new, err = us.validate(
        _form(v, **{"new.mkt": "US", "new.query": "QS"}),
        v,
        [*UNIVERSE, Listed("QS", "퀀텀스케이프", "NYSE", "STOCK")],
    )
    assert not err and new["acc.QS.removed"] is False  # 뺐던 기본 종목 다시 넣기


def test_page_shows_added_rows_and_errors(tmp_path: Path) -> None:
    _save(tmp_path, **{"new.mkt": "KR", "new.query": "KODEX 200", "new.daily": "3000"})
    v = us.load(tmp_path)
    html = render_settings(v, token="T", errors={"new.query": "찾지 못했어요"})
    assert 'name="x.KR.069500.daily" value="3,000"' in html
    assert 'popovertarget="del-x-KR-069500">삭제</button>' in html
    assert "정말 삭제하시겠습니까?" in html and 'form="del-x-KR-069500-f"' in html
    assert '<form id="del-acc-BTQ-f" method="post" action="/settings/delete">' in html
    big = html.split('<form method="post" action="/settings/review">')[1].split("</form>")[0]
    assert "/settings/delete" not in big  # 삭제 폼은 큰 폼 밖에
    assert 'name="new.query"' in html and "찾지 못했어요" in html
    assert "준비 중</span></div>" not in html.split('id="set-toss"')[1].split("</section>")[0]


def test_won_sign_and_add_button(tmp_path: Path) -> None:
    _save(tmp_path, **{"new.mkt": "KR", "new.query": "005930", "new.daily": "₩7,000"})
    v = us.load(tmp_path)
    assert v["x.KR.005930.daily"] == 7000
    html = render_settings(v, token="T")
    row = html.split('name="x.KR.005930.daily"')[0].rsplit('<div class="set-acc">', 1)[1]
    assert "<span>하루 ₩</span>" in row and "원</span>" not in row
    assert '<button type="submit" class="set-add-btn">+ 추가</button>' in html
    assert us.show("x.KR.005930.daily", 7000) == "₩7,000"


def test_delete_route(tmp_path: Path) -> None:
    import re
    import threading
    import urllib.error
    import urllib.parse
    import urllib.request

    from auto_trading.ui.server import make_server

    _save(tmp_path, **{"new.mkt": "KR", "new.query": "삼성전자", "new.daily": "1000"})
    server = make_server(tmp_path, port=0, catchup=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(form: dict[str, str]) -> int:
        req = urllib.request.Request(
            base + "/settings/delete", data=urllib.parse.urlencode(form).encode(), method="POST"
        )
        req.add_header("Origin", base)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a: object, **k: object) -> None:
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(req) as r:
                return int(r.status)
        except urllib.error.HTTPError as err:
            return err.code

    try:
        with urllib.request.urlopen(base + "/settings") as r:
            token = re.search(r'name="token" value="([^"]+)"', r.read().decode())
        assert token
        assert post({"token": "x", "key": "x.KR.005930"}) == 403
        assert post({"token": token.group(1), "key": "x.KR.999999"}) == 400
        assert post({"token": token.group(1), "key": "x.KR.005930"}) == 303
        assert "x.KR.005930.daily" not in us.load(tmp_path)
        assert us.acc_history(tmp_path, "005930")[-1][1] == 0.0
        assert post({"token": token.group(1), "key": "x.KR.005930"}) == 400  # 두 번은 안 됨
    finally:
        server.shutdown()
        server.server_close()
