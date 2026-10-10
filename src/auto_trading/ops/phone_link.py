"""휴대폰으로 보기 — Tailscale 로 이 PC 의 대시보드를 내 기기에만 연다 (2026-10-11 사용자).

* 대시보드 서버는 그대로 127.0.0.1 전용이다. ``tailscale serve`` 가 내 Tailscale 기기(같은 계정에
  로그인한 아이폰 등)에서 온 요청만 ``https://<이 PC>.<tailnet>.ts.net`` → ``http://127.0.0.1:8790``
  로 넘겨 준다. 공유기 포트를 열지 않고, 인터넷의 다른 사람은 주소를 알아도 못 들어온다.
* 휴대폰에서 온 요청은 **보기만** 된다(server.py: 저장 · 키 · 자동 실행 · 비상 정지 POST 는 403).
* Tailscale 설치 · 로그인은 사람이 한다. 여기서는 상태를 읽고 ``serve`` 를 켜고 끄기만 한다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["PHONE_PORT", "PhoneState", "serve_off", "serve_on", "state"]

PHONE_PORT = 8790  # PC 바로가기 화면(8787)과 따로 — 옛 서버가 남아 있어도 휴대폰은 최신
"""휴대폰이 보는 대시보드 포트 — 자동 실행 '휴대폰 서버' 예약(AutoTrading_Dashboard)이 띄운다."""

Runner = Callable[[Sequence[str]], str]


def find_exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES", r"C:\Program Files"), r"C:\Program Files"):
        p = Path(base) / "Tailscale" / "tailscale.exe"
        if p.exists():
            return str(p)
    return None


def _run(args: Sequence[str]) -> str:
    exe = find_exe()
    if exe is None:
        raise RuntimeError("Tailscale 이 설치되어 있지 않아요")
    done = subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip()[:400])
    return done.stdout


@dataclass(frozen=True, slots=True)
class PhoneState:
    installed: bool
    running: bool = False
    """로그인해서 연결됨 (BackendState Running)."""
    host: str = ""
    """이 PC 의 Tailscale 이름 (예: my-pc.tail1234.ts.net)."""
    serving: bool = False
    """``tailscale serve`` 가 대시보드 포트로 넘겨 주는 중."""
    phones: tuple[str, ...] = ()
    """같은 계정에 로그인해 지금 켜져 있는 휴대폰(iOS · Android) 이름."""
    error: str = ""

    @property
    def url(self) -> str:
        return f"https://{self.host}/live" if self.host else ""


def _serving(raw: str) -> bool:
    try:
        conf = json.loads(raw or "{}")
    except ValueError:
        return False
    return f"127.0.0.1:{PHONE_PORT}" in json.dumps(conf) or f"localhost:{PHONE_PORT}" in json.dumps(
        conf
    )


def state(runner: Runner | None = None, *, installed: bool | None = None) -> PhoneState:
    """Tailscale 상태 (못 읽어도 예외 없이)."""
    run = runner or _run
    if installed is None:
        installed = runner is not None or find_exe() is not None
    if not installed:
        return PhoneState(False)
    try:
        st = json.loads(run(["status", "--json"]) or "{}")
    except Exception as exc:  # 로그아웃 · 서비스 꺼짐
        return PhoneState(True, error=str(exc)[:200])
    running = st.get("BackendState") == "Running"
    host = str((st.get("Self") or {}).get("DNSName") or "").rstrip(".")
    phones = tuple(
        str(p.get("HostName") or p.get("DNSName") or "휴대폰")
        for p in (st.get("Peer") or {}).values()
        if isinstance(p, dict)
        and str(p.get("OS", "")).lower() in {"ios", "android"}
        and p.get("Online")
    )
    try:
        serving = _serving(run(["serve", "status", "--json"]))
    except Exception:
        serving = False
    return PhoneState(True, running, host, serving, phones)


def serve_on(runner: Runner | None = None) -> str:
    """내 Tailscale 기기에만 대시보드를 연다 (https 443 → 127.0.0.1:8790, 백그라운드 유지)."""
    return (runner or _run)(["serve", "--bg", "--https=443", f"http://127.0.0.1:{PHONE_PORT}"])


def serve_off(runner: Runner | None = None) -> str:
    return (runner or _run)(["serve", "--https=443", "off"])
