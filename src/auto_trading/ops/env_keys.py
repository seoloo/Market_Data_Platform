# ruff: noqa: E501 — 한국어 설명 · PowerShell 한 줄 명령이 길다
"""설정 화면 '연결 · 키' — API 키를 ``.env`` 에 저장 (2026-10-10 사용자: "***로 전부 가리고 .env 에").

* 화면에는 **값을 절대 다시 보내지 않는다.** 있는지(설정됨 · 없음)와 저장한 날만 보여 준다.
* 빈 칸으로 저장하면 그대로 둔다. 다른 줄 · 주석은 건드리지 않고 그 이름의 줄만 바꾸거나 끝에 더한다.
  임시 파일에 쓴 뒤 바꿔치기해서 쓰다가 꺼져도 ``.env`` 가 반쯤 깨지지 않는다.
* 변경 기록(``data/settings/changes.jsonl``)에는 **이름만** 남긴다. 값 · 길이 · 앞뒤 글자도 안 남긴다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from auto_trading.config.secrets import read_env_file

__all__ = ["KEYS", "SERVICES", "KeySpec", "key_status", "save_keys"]

_KST = timedelta(hours=9)


@dataclass(frozen=True, slots=True)
class KeySpec:
    env: str
    service: str
    label: str
    pattern: str = r"[A-Za-z0-9_\-.]{8,512}"


SERVICES: tuple[tuple[str, str, str], ...] = (
    (
        "toss",
        "토스증권 (Deneb · 자동 적립)",
        "토스증권 앱 → 전체 → 설정 → Open API 에서 앱을 만들고 Client ID · Secret 을 받아요. "
        "주문은 아래 '실제 주문 허용' 을 켜야 나가요.",
    ),
    (
        "binance",
        "바이낸스 (Vega, 2026-10-15 업비트에서 옮김)",
        "바이낸스 → API 관리 → API 만들기(시스템 생성). 권한은 '읽기' 와 '현물 거래' 만, 출금은 끄고, "
        "IP 제한에 이 PC 의 공인 IP 를 넣어요. 주문은 .env 의 BINANCE_LIVE_TRADING_ENABLED=true 일 때만 나가요.",
    ),
    (
        "upbit",
        "업비트 (옛 Vega)",
        "업비트 웹 → 마이페이지 → Open API 관리에서 '자산 조회 · 주문 조회 · 주문하기' 만 고르고, "
        "허용 IP 에 이 PC 의 공인 IP 를 넣어요(출금 권한은 켜지 마세요).",
    ),
    (
        "telegram",
        "텔레그램 알림",
        "텔레그램에서 @BotFather 에게 /newbot 으로 봇을 만들어 토큰을 받고, 그 봇에게 아무 말이나 "
        "보낸 뒤 대화방 번호(chat id)를 넣어요.",
    ),
    ("info", "정보 봇 (선택)", "뉴스 · 테마 · 브리핑용 두 번째 봇 토큰과 Gemini 키예요."),
    ("supabase", "Supabase 기록 DB (선택)", "Project URL 과 Secret 키(sb_secret_…)예요."),
    ("ecos", "한국은행 ECOS (선택)", "양도세 계산 환율용 인증키예요."),
)

KEYS: tuple[KeySpec, ...] = (
    KeySpec("TOSS_CLIENT_ID", "toss", "Client ID"),
    KeySpec("TOSS_CLIENT_SECRET", "toss", "Client Secret"),
    KeySpec("BINANCE_LIVE_API_KEY", "binance", "API Key"),
    KeySpec("BINANCE_LIVE_API_SECRET", "binance", "Secret Key"),
    KeySpec("UPBIT_API_KEY", "upbit", "Access Key"),
    KeySpec("UPBIT_AOU_SECRET", "upbit", "Secret Key"),
    KeySpec("TELEGRAM_BOT_TOKEN", "telegram", "봇 토큰", r"\d{5,12}:[A-Za-z0-9_\-]{20,80}"),
    KeySpec("TELEGRAM_CHAT_ID", "telegram", "대화방 번호", r"-?\d{3,20}"),
    KeySpec("TELEGRAM_INFO_BOT_TOKEN", "info", "정보 봇 토큰", r"\d{5,12}:[A-Za-z0-9_\-]{20,80}"),
    KeySpec("GEMINI_API_KEY", "info", "Gemini 키"),
    KeySpec("SUPABASE_URL", "supabase", "Project URL", r"https://[a-z0-9]{8,40}\.supabase\.co/?"),
    KeySpec("SUPABASE_SERVICE_KEY", "supabase", "Secret 키"),
    KeySpec("ECOS_API_KEY", "ecos", "인증키"),
)
_BY_ENV = {k.env: k for k in KEYS}
TOSS_SWITCH = "TOSS_TRADING_ENABLED"
"""토스는 이 값이 정확히 ``true`` 일 때만 주문한다 (config/secrets.py)."""


def _meta_path(root: Path) -> Path:
    return root / "data" / "settings" / "keys.json"


def key_status(root: Path) -> dict[str, dict[str, str | bool]]:
    """이름 → {set, saved}. **값은 돌려주지 않는다.**"""
    env = read_env_file(root / ".env")
    try:
        meta = json.loads(_meta_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    out: dict[str, dict[str, str | bool]] = {
        k.env: {"set": bool(env.get(k.env)), "saved": str(meta.get(k.env, ""))} for k in KEYS
    }
    out[TOSS_SWITCH] = {"set": env.get(TOSS_SWITCH, "").strip().lower() == "true", "saved": ""}
    return out


def validate(form: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """폼 → (바꿀 값, 오류). 빈 칸은 그대로 둠. 오류 문구에 값을 넣지 않는다."""
    updates, errors = {}, {}
    for k in KEYS:
        raw = (form.get(k.env) or "").strip()
        if not raw:
            continue
        if not re.fullmatch(k.pattern, raw):
            errors[k.env] = (
                "형식이 맞지 않아요 — 복사할 때 앞뒤 공백 · 줄바꿈이 들어갔는지 확인해 주세요"
            )
            continue
        updates[k.env] = raw.rstrip("/") if k.env == "SUPABASE_URL" else raw
    if "toss_trading" in form:
        updates[TOSS_SWITCH] = "true" if form.get("toss_trading") == "on" else "false"
    return updates, errors


def _write_env(path: Path, updates: Mapping[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    out = []
    for line in lines:
        name = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else ""
        )
        if name in updates:
            if name in seen:
                continue  # 같은 이름이 두 번 있으면 하나만 남긴다
            out.append(f"{name}={updates[name]}")
            seen.add(name)
        else:
            out.append(line)
    out += [f"{n}={v}" for n, v in updates.items() if n not in seen]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


def save_keys(root: Path, updates: Mapping[str, str], now: datetime | None = None) -> list[str]:
    """``.env`` 에 저장하고 저장한 이름을 돌려준다. 기록에는 이름만."""
    if not updates:
        return []
    now = now or datetime.now(UTC)
    _write_env(root / ".env", updates)
    stamp = (now.astimezone(UTC) + _KST).strftime("%Y-%m-%d %H:%M")
    meta_path = _meta_path(root)
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    with (meta_path.parent / "changes.jsonl").open("a", encoding="utf-8") as fh:
        for name in updates:
            meta[name] = stamp
            spec = _BY_ENV.get(name)
            what = (
                f"토스 실제 주문 허용 → {'켬' if updates[name] == 'true' else '끔'}"
                if name == TOSS_SWITCH
                else f"키 저장: {spec.label if spec else name} ({name})"
            )
            fh.write(
                json.dumps(
                    {"at": stamp, "by": "설정 화면", "key": name, "what": what}, ensure_ascii=False
                )
                + "\n"
            )
    tmp = meta_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(meta_path)
    return list(updates)
