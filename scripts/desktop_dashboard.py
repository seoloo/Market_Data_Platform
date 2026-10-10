"""오르빗 창 전용 UI 서버. 매 실행 최신 소스·빈 로컬 포트·부모 프로세스 수명에 종속.

오래된 UI·매매·예약 작업의 프로세스를 검색하거나 종료하지 않는다.
ready 파일은 앱이 만든 전용 임시 폴더에만 존재하며 계좌/키/장부 내용을 넣지 않는다.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from threading import Thread

from auto_trading.ui.server import make_server


def parent_waiter(pid: int, closed: Path) -> Callable[[], None]:
    """PID 재사용 대신 Windows 프로세스 핸들을 기다린다. 부모가 사라지면 서버를 정리한다."""
    if pid <= 0:
        raise ValueError("부모 PID가 필요합니다.")
    if os.name != "nt":
        raise RuntimeError("오르빗 데스크톱 서버는 Windows 전용입니다.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def wait() -> None:
        try:
            while not closed.exists():
                result = kernel.WaitForSingleObject(handle, 250)
                if result == 0:
                    return
                if result != 258:  # WAIT_TIMEOUT
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)

    return wait


def run(root: Path, ready: Path, wait: Callable[[], None], nonce: str = "") -> None:
    """실제 바인드 포트를 기록한다. 대기 실패에도 자기 UI만 닫는다."""
    server = make_server(root, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        ready.write_text(
            json.dumps({"port": server.server_address[1], "pid": os.getpid(), "nonce": nonce}),
            encoding="utf-8",
        )
        wait()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        ready.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--nonce", required=True)
    args = parser.parse_args()
    # Open the parent handle before binding or loading any account data.
    wait = parent_waiter(args.parent_pid, args.ready_file.parent / "closed")
    run(Path(__file__).resolve().parents[1], args.ready_file, wait, args.nonce)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
