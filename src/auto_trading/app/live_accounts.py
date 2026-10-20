"""실제 계좌 조회 창구 — 현황 동기화(``ui/live_sync``)가 쓸 업비트 · 토스 조회 (조회만).

언제 쓰나: 현황을 보여 주는 실행기(텔레그램 봇 · 대시보드 · 매일 기록 · 파이어 보고)가 시작할 때
           :func:`install` 을 한 번 부른다. 그 뒤 ``ui.live_sync.load`` 가 이 함수를 부른다.
읽고 쓰는 것: 업비트 · 토스 API (주문 꺼짐 · 조회만) · 토스 접속 토큰 ``data/toss_token.json``.

[이유] import 계약 'UI 는 매매 로직을 import 하지 않는다' — 화면(ui) 코드가 거래소 모듈을 직접
       가져다 쓰면 안 된다. 2026-10-20 현황 동기화를 붙이며 ``ui/live_sync`` 가 거래소를 직접 불러
       계약이 깨졌다(커밋 30f56b9 · ee617f7). 리팩토링 5 단계(2026-10-21)에서 조회 부분만 이 앱 계층
       모듈로 옮기고, 실행기가 넣어 주게(의존성 주입) 바꿨다. 조회 내용 · 순서는 그대로.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from auto_trading.ui import live_sync
from auto_trading.ui.live_sync import LiveAccountSnapshot

__all__ = ["install", "read_live_accounts"]


def read_live_accounts(
    root: Path, env: Mapping[str, str], order_ids: Sequence[str], want_toss_qty: bool
) -> LiveAccountSnapshot:
    """[쓰임] 업비트 잔고 · 평균가, 토스 보유 수량 · 접수 주문 체결 상태를 한 번에 읽는다.
    [이유] 한 곳이 실패해도 나머지는 읽는다 — 실패는 안내 한 줄로 (현황을 막지 않는다)."""
    snapshot = LiveAccountSnapshot()
    env_path = root / ".env"
    # 1 단계: 업비트 (Vega) — 원화 · 코인 잔고와 앱의 매수 평균가
    if env.get("UPBIT_API_KEY") and env.get("UPBIT_AOU_SECRET"):
        try:
            from auto_trading.exchange.upbit import UpbitClient

            client = UpbitClient(env["UPBIT_API_KEY"], env["UPBIT_AOU_SECRET"])
            try:
                accounts = client.accounts()
                snapshot.upbit = {
                    currency: (float(qty), float(average))
                    for currency, (qty, average) in accounts.items()
                }
            finally:
                client.close()
        except Exception as exc:  # 조회 실패로 현황을 막지 않는다
            snapshot.notes.append(f"업비트 조회 실패 — 장부 기준 ({type(exc).__name__})")
    # 2 단계: 토스 — 보유 수량 · 접수(SENT) 주문의 체결 상태
    if order_ids or want_toss_qty:
        try:
            from auto_trading.config.secrets import load_toss_credentials
            from auto_trading.exchange.toss import TossReader, TossTokenCache

            reader = TossReader(
                load_toss_credentials(env_path), TossTokenCache(root / "data" / "toss_token.json")
            )
            try:
                if want_toss_qty:
                    snapshot.toss_qty = {
                        symbol: float(position.qty)
                        for symbol, position in reader.holdings().items()
                    }
                for order_id in order_ids:
                    status = reader.order_status(order_id)
                    filled_amount = float(status.filled_amount or 0) or float(
                        status.filled_qty
                    ) * float(status.avg_price or 0)
                    snapshot.toss[order_id] = {
                        "status": status.status,
                        "filled_qty": float(status.filled_qty),
                        "filled_amount": filled_amount,
                        "commission": float(status.commission or 0),
                    }
            finally:
                reader.close()
        except Exception as exc:  # 조회 실패로 현황을 막지 않는다
            snapshot.notes.append(f"토스 주문 조회 실패 — 장부 기준 ({type(exc).__name__})")
    return snapshot


def install() -> None:
    """[쓰임] 현황 동기화에 이 조회 창구를 넣는다 (실행기 시작 때 한 번)."""
    live_sync.install_account_reader(read_live_accounts)
