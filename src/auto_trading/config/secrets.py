"""`.env` 비밀값 로더. 값은 로그·repr·예외 메시지에 절대 싣지 않는다.

`.env`는 `.gitignore` 대상이다. 키는 설정 YAML이 아니라 여기서만 읽는다 (원 명세 §17).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from auto_trading.core.errors import ConfigError

__all__ = ["BinanceCredentials", "load_binance_credentials", "read_env_file"]


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
