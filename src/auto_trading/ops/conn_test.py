# ruff: noqa: E501 — 한국어 설명 · PowerShell 한 줄 명령이 길다
"""설정 화면 '연결 시험' — 저장한 키로 조회만 해 본다 (주문 없음, 2026-10-10).

* 토스: 매수 가능 금액 조회 · 업비트: 잔고 조회 · 텔레그램: 봇 확인 + 대화방에 시험 문자 한 통 ·
  Supabase: Data API 응답.
* 결과 문구에 **키 · 금액을 넣지 않는다.** 오류도 종류만(텔레그램 주소에는 토큰이 들어 있어 원문을 안 씀).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx

from auto_trading.config.secrets import load_toss_credentials, read_env_file

__all__ = ["TESTS", "run_test"]


def _toss(root: Path) -> str:
    from auto_trading.exchange.toss import TossReader, TossTokenCache

    reader = TossReader(
        load_toss_credentials(root / ".env"), TossTokenCache(root / "data" / "toss_token.json")
    )
    reader.buying_power("USD")
    trading = load_toss_credentials(root / ".env").trading_enabled
    return "연결됨 · 잔고 조회 성공" + (
        " · 실제 주문 허용 켜짐" if trading else " · 실제 주문은 꺼져 있음"
    )


def _upbit(root: Path) -> str:
    from auto_trading.exchange.upbit import UpbitClient

    env = read_env_file(root / ".env")
    client = UpbitClient(env.get("UPBIT_API_KEY", ""), env.get("UPBIT_AOU_SECRET", ""))
    try:
        client.balances()
    finally:
        client.close()
    return "연결됨 · 잔고 조회 성공"


def _telegram(root: Path) -> str:
    env = read_env_file(root / ".env")
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        raise RuntimeError("봇 토큰 · 대화방 번호를 먼저 저장해 주세요")
    try:
        me = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
        if not me.get("ok"):
            raise RuntimeError("봇 토큰이 맞지 않아요")
        sent = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": "🔌 오르빗 연결 시험 — 이 문자가 보이면 알림이 잘 와요"},
            timeout=15,
        ).json()
    except httpx.HTTPError as exc:  # 원문에 토큰 주소가 들어 있어 종류만
        raise RuntimeError(f"텔레그램에 닿지 못했어요 ({type(exc).__name__})") from None
    if not sent.get("ok"):
        raise RuntimeError("대화방 번호가 맞지 않거나, 봇에게 먼저 말을 걸지 않았어요")
    name = (me.get("result") or {}).get("username") or "봇"
    return f"연결됨 · @{name} 이 대화방에 시험 문자를 보냈어요"


def _supabase(root: Path) -> str:
    env = read_env_file(root / ".env")
    url, key = env.get("SUPABASE_URL", "").rstrip("/"), env.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise RuntimeError("Project URL · Secret 키를 먼저 저장해 주세요")
    r = httpx.get(f"{url}/rest/v1/", headers={"apikey": key}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"응답 {r.status_code} — 키 · 주소를 확인해 주세요")
    return "연결됨 · Data API 응답"


TESTS: dict[str, Callable[[Path], str]] = {
    "toss": _toss,
    "upbit": _upbit,
    "telegram": _telegram,
    "supabase": _supabase,
}


def run_test(root: Path, service: str) -> tuple[bool, str]:
    """(성공, 한 줄). 예외 원문은 키가 섞일 수 있어 우리가 만든 문구 · 종류만 쓴다."""
    fn = TESTS.get(service)
    if fn is None:
        return False, "시험할 수 없는 항목이에요"
    try:
        return True, fn(root)
    except RuntimeError as exc:
        return False, str(exc)
    except Exception as exc:
        text = str(exc)
        # 우리 코드의 설정 오류 문구(이름만 들어 있음)는 보여 주고, 그 밖은 종류만
        if type(exc).__name__ in {"ConfigError", "TossApiError"} and "=" not in text:
            return False, text[:160]
        return False, f"실패 ({type(exc).__name__}) — 키 · 허용 IP 를 확인해 주세요"
