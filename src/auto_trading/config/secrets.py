"""`.env` 비밀값 로더. 값은 로그·repr·예외 메시지에 절대 싣지 않는다.

`.env`는 `.gitignore` 대상이다. 키는 설정 YAML이 아니라 여기서만 읽는다 (원 명세 §17).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from auto_trading.core.errors import ConfigError

__all__ = [
    "BinanceCredentials",
    "KisCredentials",
    "OpenAIKey",
    "TelegramCredentials",
    "load_binance_credentials",
    "load_gemini_key",
    "load_kis_credentials",
    "load_openai_key",
    "load_telegram_credentials",
    "load_telegram_info_credentials",
    "read_env_file",
]


def read_env_file(path: Path | str = ".env") -> dict[str, str]:
    """``KEY=VALUE`` 줄만 읽는다. 빈 줄·``#`` 주석·값 뒤 공백+``#`` 주석은 무시한다."""
    file = Path(path)
    if not file.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        for marker in (" #", "\t#"):
            if marker in value:
                value = value.split(marker, 1)[0]
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True, slots=True)
class BinanceCredentials:
    api_key: str
    api_secret: str

    def __repr__(self) -> str:
        return f"BinanceCredentials(api_key={self.api_key[:4]}…, api_secret=***)"

    __str__ = __repr__


def load_binance_credentials(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> BinanceCredentials:
    """환경변수가 `.env`보다 우선한다. 없으면 ConfigError (값은 메시지에 넣지 않는다)."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    key = merged.get("BINANCE_API_KEY", "")
    secret = merged.get("BINANCE_API_SECRET", "")
    if not key or not secret:
        raise ConfigError("BINANCE_API_KEY / BINANCE_API_SECRET 가 .env 또는 환경변수에 없다")
    return BinanceCredentials(key, secret)


@dataclass(frozen=True, slots=True)
class TelegramCredentials:
    bot_token: str
    chat_id: str

    def __repr__(self) -> str:
        return f"TelegramCredentials(bot_token=***, chat_id={self.chat_id[:3]}…)"

    __str__ = __repr__


def load_telegram_credentials(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> TelegramCredentials | None:
    """텔레그램은 선택 사항이다. 토큰·채팅 ID 중 하나라도 비면 ``None`` (알림은 로컬 기록만)."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    token = merged.get("TELEGRAM_BOT_TOKEN", "")
    chat = merged.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return None
    return TelegramCredentials(token, chat)


def load_telegram_info_credentials(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> TelegramCredentials | None:
    """정보 봇(테마 · 시장 브리핑 · 종목 뉴스, 2026-10-10).

    ``TELEGRAM_INFO_BOT_TOKEN`` + 같은 채팅.
    없으면 투자 봇으로 돌아간다 (알림이 사라지지 않게)."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    token = merged.get("TELEGRAM_INFO_BOT_TOKEN", "")
    chat = merged.get("TELEGRAM_CHAT_ID", "")
    if token and chat:
        return TelegramCredentials(token, chat)
    return load_telegram_credentials(path, environ)


@dataclass(frozen=True, slots=True)
class KisCredentials:
    """한국투자증권 Open API (실전). 계좌번호는 앞 8자리(CANO) + 상품코드 2자리."""

    app_key: str
    app_secret: str
    cano: str
    product_code: str
    trading_enabled: bool

    def __repr__(self) -> str:
        return (
            "KisCredentials(app_key=***, app_secret=***, "
            f"account=****{self.cano[-2:]}-{self.product_code}, "
            f"trading_enabled={self.trading_enabled})"
        )

    __str__ = __repr__


def load_kis_credentials(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> KisCredentials:
    """KIS 자격: ``KIS_APP_KEY``·``KIS_APP_SECRET``·``KIS_ACCOUNT_NO``·``KIS_TRADING_ENABLED``.

    계좌번호는 숫자 10자리(``-`` 허용).

    빠진 이름을 모두 적어 ConfigError 를 낸다(값은 메시지에 넣지 않는다).
    ``KIS_TRADING_ENABLED`` 는 정확히 ``true`` 일 때만 참이다 — 그 밖의 값·누락은 거짓(주문 차단).
    """
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    names = ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO")
    missing = [n for n in names if not merged.get(n, "").strip()]
    if missing:
        raise ConfigError(f"{' / '.join(missing)} 가 .env 또는 환경변수에 없다")
    account = merged["KIS_ACCOUNT_NO"].replace("-", "").strip()
    if len(account) != 10 or not account.isdigit():
        raise ConfigError("KIS_ACCOUNT_NO 는 숫자 10자리(앞 8자리 계좌 + 상품코드 2자리)여야 한다")
    return KisCredentials(
        app_key=merged["KIS_APP_KEY"].strip(),
        app_secret=merged["KIS_APP_SECRET"].strip(),
        cano=account[:8],
        product_code=account[8:],
        trading_enabled=merged.get("KIS_TRADING_ENABLED", "").strip().lower() == "true",
    )


@dataclass(frozen=True, slots=True)
class OpenAIKey:
    """AI API 키(ChatGPT·Gemini) — 문서 115 뉴스·AI 판단 기록용. 값은 보여 주지 않는다."""

    value: str

    def __repr__(self) -> str:
        return "OpenAIKey(***)"

    __str__ = __repr__


def load_openai_key(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> OpenAIKey | None:
    """``OPENAI_API_KEY`` 또는 ``CHATGPT_KEY`` (앞의 것 우선). 없으면 ``None`` (AI 판단 건너뜀)."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    for name in ("OPENAI_API_KEY", "CHATGPT_KEY"):
        value = merged.get(name, "").strip()
        if value:
            return OpenAIKey(value)
    return None


def load_gemini_key(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> OpenAIKey | None:
    """``GEMINI_API_KEY`` (Google AI Studio 무료 등급). 없으면 ``None``. 값은 보여 주지 않는다."""
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    value = merged.get("GEMINI_API_KEY", "").strip()
    return OpenAIKey(value) if value else None


@dataclass(frozen=True, slots=True)
class TossCredentials:
    """토스증권 Open API (문서 118 §6). 값은 보여 주지 않는다."""

    client_id: str
    client_secret: str
    account_seq: int
    trading_enabled: bool

    def __repr__(self) -> str:
        return (
            f"TossCredentials(client_id=***, client_secret=***, account_seq={self.account_seq}, "
            f"trading_enabled={self.trading_enabled})"
        )

    __str__ = __repr__


def load_toss_credentials(
    path: Path | str = ".env", environ: Mapping[str, str] | None = None
) -> TossCredentials:
    """``TOSS_CLIENT_ID``·``TOSS_CLIENT_SECRET`` (+ 선택 ``TOSS_ACCOUNT_SEQ``, 기본 1).

    ``TOSS_TRADING_ENABLED`` 가 정확히 ``true`` 일 때만 주문한다 — 사용자가 직접 켠다.
    """
    merged = {**read_env_file(path), **(dict(os.environ) if environ is None else dict(environ))}
    names = ("TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET")
    missing = [n for n in names if not merged.get(n, "").strip()]
    if missing:
        raise ConfigError(f"{' / '.join(missing)} 가 .env 또는 환경변수에 없다")
    seq = merged.get("TOSS_ACCOUNT_SEQ", "1").strip() or "1"
    if not seq.isdigit():
        raise ConfigError("TOSS_ACCOUNT_SEQ 는 숫자여야 한다")
    return TossCredentials(
        client_id=merged["TOSS_CLIENT_ID"].strip(),
        client_secret=merged["TOSS_CLIENT_SECRET"].strip(),
        account_seq=int(seq),
        trading_enabled=merged.get("TOSS_TRADING_ENABLED", "").strip().lower() == "true",
    )
