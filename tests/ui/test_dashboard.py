"""운영 대시보드 (읽기 전용): 데이터 읽기·화면·서버 안전 원칙."""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from auto_trading.backtest.overlay import O2_STOP
from auto_trading.execution.paper_overlay import PaperOverlayRunner
from auto_trading.ops.catchup import Task
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository
from auto_trading.ui.catchup import CatchupRunner
from auto_trading.ui.charts import Series, line_chart
from auto_trading.ui.data import load_dashboard
from auto_trading.ui.render import PAGES, render_page
from auto_trading.ui.server import make_server
from tests.execution.test_paper_overlay import P0, FakeMarket, now_after

D = 86_400_000
EVIL = "<script>alert(1)</script>"


def build_root(tmp_path: Path) -> Path:
    """Paper 원장 3개·훈련 기록·점검 기록·연구 기록이 있는 가짜 저장소."""
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path / "data" / "trading.db")
    apply_migrations(conn, now_ms=1)
    t2 = PaperDayRepository(conn)
    for k, equity in enumerate(("10000", "10050.5", "9990")):
        t2.insert(
            PaperDay(
                strategy_id="t2_long",
                decision_ms=P0 + (k + 1) * D - 1,
                status="OK",
                equity=Decimal(equity),
                holdings={"BTC": "0.05", "USDT": "5000"},
                prices={"BTCUSDT": "90000", "ETHUSDT": "3000"},
                targets={"BTCUSDT": "0.4", "ETHUSDT": None},
                executed=[],
                note=EVIL,
                created_ms=1,
            )
        )
    overlay = OverlayPaperDayRepository(conn)
    market = FakeMarket()
    for cfg, sid in ((None, "o1_overlay"), (O2_STOP, "o2_stop")):
        runner = PaperOverlayRunner(market, overlay, clock=lambda: 1, cfg=cfg, strategy_id=sid)
        for k in range(10):
            runner.run_once(now_after(P0 + k * D))
    conn.close()
    testnet = tmp_path / "reports" / "testnet"
    testnet.mkdir(parents=True)
    (testnet / "drill-20260925T001500Z.json").write_text(
        json.dumps({"at": "20260925T001500Z", "ok": True, "checks": [{"step": "a", "pass": True}]}),
        encoding="utf-8",
    )
    (testnet / "stream-20260925T030000Z.json").write_text(
        json.dumps(
            {
                "at": "20260925T030000Z",
                "ok": True,
                "outcome": "TRIGGERED",
                "checks": [{"step": "재연결·재조회", "pass": True, "detail": EVIL}],
            }
        ),
        encoding="utf-8",
    )
    (testnet / "stream-broken.json").write_text("{not json", encoding="utf-8")
    watch = tmp_path / "reports" / "watchdog"
    watch.mkdir()
    (watch / "2026-09-25.json").write_text(
        json.dumps(
            {
                "date": "2026-09-25",
                "exit_code": 1,
                "findings": [{"level": "WARN", "ledger": "O/v1", "message": EVIL}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "cash_yield.yaml").write_text(
        "margin_reserve: 0.5\n"
        "rates:\n"
        "  - from: 2020-01-01\n"
        "    source: test\n"
        "    tiers:\n"
        "      - {up_to: 1000, apr: 0.0708}\n"
        "      - {up_to: null, apr: 0.0308}\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs" / "current"
    docs.mkdir(parents=True)
    (docs / "RESEARCH_HISTORY.md").write_text(
        "# 연구\n\n## T — PASS (2026-09-24)\n\n판정 **통과**\n\n"
        f"## X{EVIL} — REJECTED (2026-09-20)\n\nFailure: 비용\n\n## 비용 이력\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return build_root(tmp_path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestData:
    def test_reads_all_sources_without_writing(self, root: Path) -> None:
        db = root / "data" / "trading.db"
        before = digest(db)
        data = load_dashboard(root, P0 + 11 * D)
        assert digest(db) == before  # 읽기만 했다
        assert [lg.key for lg in data.ledgers] == ["t2", "o1", "o2"]
        assert data.ledgers[0].equity == 9990.0
        assert len(data.ledgers[1].points) == 10
        assert len(data.benchmark) == 10 and data.benchmark[0].equity == 10_000.0
        assert [d.kind for d in data.drills] == ["drill", "stream"]
        assert data.watch[0].exit_code == 1
        assert [(r.title, r.verdict) for r in data.research] == [
            ("T", "PASS"),
            (f"X{EVIL}", "REJECTED"),
        ]
        assert data.research[0].summary == "판정 통과"  # 마크다운 기호 제거
        assert any("stream-broken.json" in e for e in data.errors)  # 손상 파일은 건너뛰고 알린다

    def test_cash_yield_assumption_is_added_beside_the_ledger(self, root: Path) -> None:
        data = load_dashboard(root, P0 + 11 * D)
        assert "3.08%" in data.cash_yield
        for lg in data.ledgers:
            assert len(lg.with_yield) == len(lg.points)
            assert lg.with_yield[0].equity == lg.points[0].equity  # 첫날은 원장과 같다
            assert lg.with_yield[-1].equity > lg.points[-1].equity  # 유휴 현금이 있었다
            assert all(0.0 <= x <= 1.0 for x in lg.idle)
        html = render_page(data, "compare")
        assert "현금 이자 가정" in html and "실제 예치는 없다" in html

    def test_no_cash_yield_file_means_no_assumption(self, root: Path) -> None:
        (root / "config" / "cash_yield.yaml").unlink()
        data = load_dashboard(root, P0 + 11 * D)
        assert data.cash_yield == "" and all(not lg.with_yield for lg in data.ledgers)

    def test_missing_db_is_reported_not_created(self, tmp_path: Path) -> None:
        data = load_dashboard(tmp_path, 0)
        assert data.ledgers == ()
        assert any("DB" in e for e in data.errors)
        assert not (tmp_path / "data" / "trading.db").exists()


class TestRender:
    @pytest.mark.parametrize("page", [key for key, _, _ in PAGES])
    def test_every_page_renders_and_escapes(self, root: Path, page: str) -> None:
        html = render_page(load_dashboard(root, P0 + 11 * D), page)
        assert html.startswith("<!doctype html>")
        assert "읽기 전용" in html
        if page == "toss":
            assert "저장된 적립 기록" in html and "실거래 비활성</span>" not in html
        elif page in ("lt_paper", "qs_paper", "ag_paper", "kr_shadow"):
            assert "저장된 Paper 기록" in html and "실거래 비활성</span>" not in html
        else:
            assert "실거래 비활성" in html
        assert EVIL not in html  # 모든 동적 문자열은 이스케이프
        assert "<form" not in html and "<script" not in html

    def test_stock_pages_have_separate_navigation_group(self, root: Path) -> None:
        html = render_page(load_dashboard(root, P0 + 11 * D), "toss")
        nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        assert '<div class="nav-heading">자동매매</div>' in nav
        assert '<div class="nav-heading">해외 주식</div>' in nav
        assert nav.count('href="/toss"') == 1
        assert 'href="/overseas"' not in nav and 'href="/accumulate"' not in nav
        assert "QNDX" in html and "BTQ" in html and "WQTM" in html

    def test_lt_paper_page_reads_saved_report(self, root: Path) -> None:
        import json

        rep = root / "reports" / "lt_paper"
        rep.mkdir(parents=True)
        (rep / "latest.json").write_text(
            json.dumps(
                {
                    "date": "2026-10-02",
                    "equity_usd": 997.5,
                    "start_usd": 1000.0,
                    "return_since_start": -0.0025,
                    "cash_usd": 0.0,
                    "positions": [
                        {
                            "symbol": EVIL,
                            "entry": "2026-10-02",
                            "cost_usd": 100,
                            "value_usd": 99.75,
                            "return": -0.0025,
                            "weight": 0.1,
                            "from_peak": 0.0,
                        }
                    ],
                    "pending_sell_next_session": {"AMD": "trend_off"},
                    "leaders_as_of": "2026-10-01",
                    "leaders": [{"symbol": "HPE", "score": 0.99, "trend_up": True}],
                    "leading_sectors": ["Information Technology"],
                    "spy_above_200": True,
                    "breadth_above_50ma": 0.247,
                    "news_shadow": {
                        "items": 529,
                        "sources_ok": 16,
                        "sources_failed": [],
                        "fetched_at": "t",
                    },
                }
            ),
            encoding="utf-8",
        )
        html = render_page(load_dashboard(root, P0 + 11 * D), "lt_paper")
        assert "주도주 추세 보유" in html and "HPE" in html and "$997.50" in html
        assert "추세 꺾임" in html and "뉴스 529 건" in html

    def test_kr_shadow_page_shows_three_lists_paper_and_history(self, root: Path) -> None:
        import json

        rep = root / "reports" / "kr_shadow"
        rep.mkdir(parents=True)
        leader = {"code": "005930", "name": EVIL, "sector": "IT", "score": 0.9, "trend": True}
        day1 = {
            "date": "2026-10-02",
            "universe": 200,
            "kospi_above_200d": True,
            "universe_median_6m": 0.012,
            "flow_coverage": 200,
            "price_leaders": [leader],
            "flow_leaders": [{**leader, "name": "S-Oil", "code": "010950"}],
            "price_sectors": [{"sector": "에너지", "median": 0.87, "n": 2}],
            "flow_sectors": [],
            "news": {"items": 595, "fetched_at": "t", "sources_failed": []},
            "ai": {
                "model": "gemini:x",
                "market_room": "neutral",
                "confidence": 0.65,
                "themes": ["반도체", "화장품"],
                "leaders": [{"code": "000660", "name": "SK하이닉스"}],
                "summary": "요약",
                "overlap_price": [],
                "overlap_flow": [],
            },
        }
        day2 = {
            **day1,
            "date": "2026-11-03",
            "paper": {
                "start": "2026-10-06",
                "price_rule": {
                    "value_krw": 10_100_000,
                    "positions": [{"name": "광전자", "entry": "2026-11-02", "return": 0.01}],
                    "sell_next_close": {"017900": "stop_35"},
                },
                "flow_rule": {"value_krw": 9_900_000, "positions": [], "sell_next_close": {}},
                "equal_weight_krw": 10_000_000,
            },
        }
        for d in (day1, day2):
            (rep / f"{d['date']}.json").write_text(json.dumps(d), encoding="utf-8")
        (rep / "latest.json").write_text(json.dumps(day2), encoding="utf-8")
        html = render_page(load_dashboard(root, P0 + 11 * D), "kr_shadow")
        assert "국장 주도주" in html and "S-Oil" in html and "SK하이닉스" in html
        assert "반도체" in html and "중립" in html and "뉴스 595 건" in html
        assert "10,100,000원" in html and "고점 −35%" in html and "광전자" in html
        assert html.count("2026-10-02") >= 1 and "2026-11-03" in html  # 날짜별 기록
        nav = html.split("<main>")[0]
        assert '<div class="nav-heading">국내 주식</div>' in nav
        assert nav.count('href="/kr-shadow"') == 1
        assert EVIL not in html
        nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        assert nav.count('href="/lt-paper"') == 1

    def test_qs_paper_page_reads_saved_report(self, root: Path) -> None:
        import json

        rep = root / "reports" / "qs_paper"
        rep.mkdir(parents=True)
        book = {
            "value_usd": 997.5,
            "invested_usd": 1000.0,
            "return_on_invested": -0.0025,
            "qqq_usd": 997.5,
            "tqqq_usd": 0.0,
            "cash_usd": 0.0,
            "tax_paid_usd": 0.0,
            "today": [EVIL],
            "next_session_if_trend_holds": "없음",
        }
        (rep / "latest.json").write_text(
            json.dumps(
                {
                    "date": "2026-10-02",
                    "qqq_close": 749.58,
                    "tqqq_close": 81.01,
                    "trend_for_next_session": {"as_of": "2026-10-02", "on": True, "r20": 0.04},
                    "books": {"QS": book, "A": book, "AG": {**book, "value_usd": 1234.5}},
                }
            ),
            encoding="utf-8",
        )
        state = root / "data" / "qs_paper"
        state.mkdir(parents=True)
        (state / "state.json").write_text(
            json.dumps(
                {
                    "books": {
                        "AG": {
                            "equity": [["2026-10-02", 997.5]],
                            "trades": [{"date": "2026-10-02", "side": "BUY_TQQQ", "usd": 1000}],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        html = render_page(load_dashboard(root, P0 + 11 * D), "qs_paper")
        assert "TQQQ 전환" in html and "켜짐" in html and "$1,234.50" in html
        assert "TQQQ 매수" in html
        assert EVIL not in html
        nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        assert nav.count('href="/qs-paper"') == 1

    def test_ag_paper_page_reads_saved_report(self, root: Path) -> None:
        import json

        rep = root / "reports" / "ag_paper"
        rep.mkdir(parents=True)
        (rep / "latest.json").write_text(
            json.dumps(
                {
                    "date": "2026-10-03",
                    "books": {
                        "AG-US": {"value_usd": 997.5, "return": -0.0025, "trades": []},
                        "AG-CR": {
                            "value_usd": 1012.3,
                            "exposure": 1.87,
                            "status": EVIL,
                            "next_target_weights": {"BTCUSDT": 0.36},
                            "multiple": 3,
                        },
                        "AG-CR4": {"multiple": 4, "next_target_weights": {"ETHUSDT": 0.25}},
                        "AG-MIX": {"value_usd": 280.0, "stopped": "2026-10-03"},
                    },
                    "paths": {"AG-CR": {"2026-10-02": 1000.0, "2026-10-03": 1012.3}},
                }
            ),
            encoding="utf-8",
        )
        html = render_page(load_dashboard(root, P0 + 11 * D), "ag_paper")
        assert "소액 공격형" in html and "$1,012.30" in html and "BTC 108%" in html
        assert "정리됨 2026-10-03" in html
        assert "ETH 100%" in html and "AG-MIX4" in html
        assert EVIL not in html
        nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        assert nav.count('href="/ag-paper"') == 1

    def test_toss_page_reads_saved_reports(self, root: Path) -> None:
        import json

        rep = root / "reports" / "toss"
        (rep / "QNDX").mkdir(parents=True)
        (rep / "QS_SWITCH").mkdir(parents=True)
        (rep / "QNDX" / "2026-10-06.json").write_text(
            json.dumps(
                {
                    "at_ms": 1_790_000_000_000,
                    "date": "2026-10-06",
                    "symbol": "QNDX",
                    "status": "PAUSE",
                    "advice": {"currency": "KRW", "accrued": 50000, "price": 34350},
                    "holding_qty": 1,
                }
            ),
            encoding="utf-8",
        )
        (rep / "QS_SWITCH" / "2026-10-06.json").write_text(
            json.dumps(
                {
                    "date": "2026-10-06",
                    "trend_on": False,
                    "as_of": "2026-10-05",
                    "parts": {"r20": -0.03, "r60": 0.01, "r120": 0.1},
                    "want": "NONE",
                    "qndx_paused": True,
                    "strategy_tqqq_qty": 0,
                    "net_krw": 0,
                    "order_line": EVIL,
                }
            ),
            encoding="utf-8",
        )
        (rep / "fx-2026-10-06-0900.json").write_text(
            json.dumps(
                {
                    "at": "2026-10-06T09:00",
                    "needs": [{"symbol": "WQTM", "qty": 1}],
                    "usd": 2.05,
                    "short_usd": 31.2,
                    "fx": 1350,
                }
            ),
            encoding="utf-8",
        )
        html = render_page(load_dashboard(root, P0 + 11 * D), "toss")
        assert "토스증권 · 적립 현황" in html and "QS 멈춤" in html and "추세 꺼짐" in html
        assert "$31 환전 필요" in html and "WQTM" in html
        assert EVIL not in html
        nav = html.split("<nav>", 1)[1].split("</nav>", 1)[0]
        assert nav.count('href="/toss"') == 1

    def test_overview_has_draft_sections(self, root: Path) -> None:
        html = render_page(load_dashboard(root, P0 + 11 * D), "overview")
        for section in ("전략별 자산 변화", "시스템 상태", "테스트넷 검증", "포지션 · 보호 상태"):
            assert section in html
        assert "합산하지 않는다" in html
        for section in ("오늘의 운영", "확인할 일", "다가오는 일정", "해외 주식 적립"):
            assert section in html

    def test_toss_initial_cards_do_not_fabricate_account_values(self, root: Path) -> None:
        state = root / "data" / "toss"
        state.mkdir()
        for symbol in ("QNDX", "BTQ", "WQTM"):
            (state / f"{symbol}.json").write_text(
                json.dumps({"start_date": "2026-10-04"}),
                encoding="utf-8",
            )
        before = digest(root / "data" / "trading.db")
        html = render_page(load_dashboard(root, P0 + 11 * D), "toss")
        assert html.count('class="savings-card ') == 4
        assert html.count("첫 실행 대기") == 4
        assert 'role="progressbar"' not in html
        assert "매일 1만 원" in html and "매일 $1" in html and "매일 $4" in html
        assert 'id="qs"' in html and 'id="fx"' in html
        assert digest(root / "data" / "trading.db") == before

    def test_toss_saved_progress_and_detail_keep_the_same_source(self, root: Path) -> None:
        rep = root / "reports" / "toss" / "QNDX"
        rep.mkdir(parents=True)
        path = rep / "2026-10-04.json"
        path.write_text(
            json.dumps(
                {
                    "status": "WAIT",
                    "date": "2026-10-04",
                    "advice": {"accrued": 10000, "price": 40000, "currency": "KRW"},
                    "pnl": 99887766,
                }
            ),
            encoding="utf-8",
        )
        before = digest(path)
        html = render_page(load_dashboard(root, P0 + 11 * D), "toss")
        assert 'aria-valuenow="25.0"' in html and "10,000원" in html
        assert 'id="qndx-details"' in html and 'id="qndx-history"' in html
        assert "99887766" not in html and digest(path) == before

    def test_toss_corrupt_report_is_not_shown_as_initial_wait(self, root: Path) -> None:
        rep = root / "reports" / "toss" / "QNDX"
        rep.mkdir(parents=True)
        (rep / "2026-10-04.json").write_text("broken", encoding="utf-8")
        html = render_page(load_dashboard(root, P0 + 11 * D), "toss")
        assert "기록 읽기 실패" in html and html.count("첫 실행 대기") == 3

    def test_brand_and_favicon_present_on_all_pages(self, root: Path) -> None:
        for page, _, _ in PAGES:
            html = render_page(load_dashboard(root, P0 + 11 * D), page)
            assert 'src="/assets/auto-trading.svg"' in html
            assert 'rel="icon" type="image/svg+xml"' in html
            assert 'href="/favicon.ico?v=2"' in html

    @pytest.mark.parametrize("page", [key for key, _, _ in PAGES])
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def test_both_themes_on_every_page(self, root: Path, page: str, theme: str) -> None:
        html = render_page(load_dashboard(root, P0 + 11 * D), page, theme=theme)
        assert f'data-theme="{theme}"' in html
        assert "라이트 모드" in html and "다크 모드" in html
        assert '<div class="topbar">' in html
        assert "<script" not in html and EVIL not in html

    def test_invalid_theme_is_not_injected(self, root: Path) -> None:
        html = render_page(load_dashboard(root, P0 + 11 * D), "overview", theme=EVIL)
        assert 'data-theme="light"' in html and EVIL not in html

    def test_unknown_page(self, root: Path) -> None:
        with pytest.raises(KeyError):
            render_page(load_dashboard(root, 0), "admin")


class TestChart:
    def test_needs_two_points(self) -> None:
        assert "2일째부터" in line_chart([Series("a", "red", ((0, 100.0),))])

    def test_draws_polyline_per_series(self) -> None:
        svg = line_chart(
            [
                Series("a", "red", ((0, 100.0), (D, 101.0))),
                Series("b", "blue", ((0, 100.0), (D, 99.0))),
            ]
        )
        assert svg.count("<polyline") == 2
        assert svg.count(">01.01<") == 1  # 같은 날짜 눈금은 한 번만


_KST = timezone(timedelta(hours=9))
_MISSED = Task(
    "AutoTrading_Paper_T2", ("21:00",), datetime(2026, 9, 30, 21, 0, tzinfo=_KST), 0, False
)


class TestServer:
    @pytest.fixture
    def runner(self, root: Path) -> CatchupRunner:
        """작업 스케줄러 대신 가짜 — 어젯밤 21:00 Paper_T2 를 놓쳤다."""
        started: list[list[str]] = []

        def run(names: list[str], **_: object) -> None:
            started.append(names)

        morning = datetime(2026, 10, 2, 9, 0, tzinfo=_KST)
        r = CatchupRunner(root, query=lambda: [_MISSED], run=run, clock=lambda: morning)
        r.started = started  # type: ignore[attr-defined]
        return r

    @pytest.fixture
    def base(self, root: Path, runner: CatchupRunner) -> Iterator[str]:
        server = make_server(root, port=0, catchup=runner)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    @staticmethod
    def _post(base: str, token: str, origin: str | None = None) -> int:
        body = urllib.parse.urlencode({"token": token}).encode()
        request = urllib.request.Request(base + "/catchup", data=body, method="POST")
        request.add_header("Origin", origin or base)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args: object, **kwargs: object) -> None:
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(request) as response:
                return int(response.status)
        except urllib.error.HTTPError as err:
            return err.code

    def test_refuses_non_local_bind(self, root: Path) -> None:
        with pytest.raises(ValueError, match=r"127.0.0.1"):
            make_server(root, host="0.0.0.0", port=0)

    def test_get_pages_with_safety_headers(self, base: str) -> None:
        for _, href, _ in PAGES:
            with urllib.request.urlopen(base + href) as response:
                assert response.status == 200
                assert "form-action 'self'" in response.headers["Content-Security-Policy"]
                assert response.headers["Cache-Control"] == "no-store"

    @pytest.mark.parametrize(
        "path,mime",
        [
            ("/favicon.ico", "image/x-icon"),
            ("/assets/auto-trading.ico", "image/x-icon"),
            ("/assets/auto-trading.svg?v=2", "image/svg+xml"),
        ],
    )
    def test_brand_assets_do_not_load_account_data(
        self,
        base: str,
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        mime: str,
    ) -> None:
        def blocked(*_: object) -> None:
            raise AssertionError("아이콘 요청은 DB·계좌 자료를 읽으면 안 됨")

        monkeypatch.setattr("auto_trading.ui.server.load_dashboard", blocked)
        with urllib.request.urlopen(base + path) as response:
            assert response.status == 200 and response.headers["Content-Type"] == mime
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert "default-src 'none'" in response.headers["Content-Security-Policy"]
            body = response.read()
            assert body.startswith(b"<svg") if "svg" in mime else body[:4] == b"\x00\x00\x01\x00"

    @pytest.mark.parametrize(
        "path",
        [
            "/assets/../server.py",
            "/assets/%2e%2e/server.py",
            "/assets/.env",
            "/assets/other.svg",
        ],
    )
    def test_asset_requests_are_exactly_whitelisted(self, base: str, path: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + path)
        assert err.value.code == 404

    def test_theme_cookie_persists_across_navigation_and_refresh(self, base: str) -> None:
        from http.cookiejar import CookieJar

        jar = CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with browser.open(base + "/?theme=dark") as response:
            assert 'data-theme="dark"' in response.read().decode()
            cookie = response.headers["Set-Cookie"]
            assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
        for path in ("/", "/toss", "/running", "/research", "/compare", "/"):
            with browser.open(base + path) as response:
                assert 'data-theme="dark"' in response.read().decode()
        with browser.open(base + "/toss?theme=light") as response:
            assert 'data-theme="light"' in response.read().decode()
        with browser.open(base + "/") as response:
            assert 'data-theme="light"' in response.read().decode()

    @pytest.mark.parametrize("cookie", ["dashboard_theme=garbage", 'dashboard_theme="bad"'])
    def test_invalid_theme_cookie_and_query_fall_back_safely(self, base: str, cookie: str) -> None:
        request = urllib.request.Request(base + "/?theme=" + urllib.parse.quote(EVIL))
        request.add_header("Cookie", cookie)
        with urllib.request.urlopen(request) as response:
            html = response.read().decode()
            assert 'data-theme="light"' in html and EVIL not in html
            assert response.headers.get("Set-Cookie") is None

    def test_theme_change_does_not_start_tasks(self, base: str, runner: CatchupRunner) -> None:
        with urllib.request.urlopen(base + "/running?theme=dark") as response:
            assert response.status == 200
        assert runner.started == []  # type: ignore[attr-defined]

    def test_unknown_path_is_404(self, base: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(base + "/admin")
        assert err.value.code == 404

    @pytest.mark.parametrize("path", ["/accumulate", "/qndx", "/overseas"])
    def test_previous_accumulation_links_still_work(self, base: str, path: str) -> None:
        with urllib.request.urlopen(base + path) as response:
            html = response.read().decode("utf-8")
        assert response.status == 200
        assert "토스증권 · 적립 현황" in html

    @pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
    def test_write_methods_are_rejected(self, base: str, method: str) -> None:
        request = urllib.request.Request(base + "/", data=b"x", method=method)
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(request)
        assert err.value.code == 501

    def test_post_elsewhere_is_404(self, base: str) -> None:
        request = urllib.request.Request(base + "/", data=b"token=x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(request)
        assert err.value.code == 404

    def test_running_page_shows_missed_task_and_button(self, base: str) -> None:
        with urllib.request.urlopen(base + "/running") as response:
            html = response.read().decode("utf-8")
        assert "놓친 작업 다시 실행" in html
        assert "Paper_T2" in html and "놓침" in html
        assert 'action="/catchup"' in html

    def test_catchup_needs_token_and_same_origin(self, base: str, runner: CatchupRunner) -> None:
        started = runner.started  # type: ignore[attr-defined]
        assert self._post(base, "wrong") == 403
        assert self._post(base, runner.token, origin="http://evil.example") == 403
        assert self._post(base, runner.token, origin="null") == 403
        assert started == []
        assert self._post(base, runner.token) == 303
        assert runner._thread is not None
        runner._thread.join(5)
        assert started == [["AutoTrading_Paper_T2"]]
