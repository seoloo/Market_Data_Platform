"""상태 화면 만들기 · 보기 — 레이크 · 실행 기록을 읽어 HTML 한 장으로.

언제 쓰나: 파이프라인이 끝난 뒤 (Airflow 마지막 작업 · 샘플 시연 끝), 또는 손으로::

    python -m mdp.ui.report                       # <lake>/_ui/index.html 을 만든다
    python -m mdp.ui.report --serve 8765          # http://127.0.0.1:8765 — 열 때마다 새로 읽는다

읽고 쓰는 것: Delta 표 · PostgreSQL (읽기만, :mod:`mdp.ui.data`) → HTML 파일 하나 (``--out``).

[이유] ``--serve`` 는 이 PC(127.0.0.1)에서만 열리고, 요청마다 다시 읽어 그린다 — 캐시가
       없어 화면이 늘 지금 상태다. 쓰기 경로가 없다(읽기 전용 화면).
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mdp.bronze.fetch import log_json, setup_cli_logging
from mdp.spark.session import lake_root
from mdp.ui.data import collect
from mdp.ui.render import render

__all__ = ["build", "main"]


def build(pg_dsn: str | None, out: Path) -> Path:
    """[쓰임] 화면을 한 번 만들어 ``out`` 에 쓰고 그 경로를 돌려준다."""
    html = render(collect(pg_dsn))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out


def _serve(pg_dsn: str | None, port: int) -> None:
    """[쓰임] 이 PC 에서만 열리는 보기 — ``/`` 를 열 때마다 새로 읽어 그린다."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = render(collect(pg_dsn)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    log_json(event="serving", url=f"http://127.0.0.1:{port}/")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.ui.report")
    parser.add_argument("--out", type=Path, help="기본: <lake>/_ui/index.html")
    parser.add_argument("--serve", type=int, metavar="PORT", help="파일 대신 이 PC 에서 보기")
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    if args.serve:
        _serve(args.pg_dsn, args.serve)
        return 0
    out = build(args.pg_dsn, args.out or lake_root() / "_ui" / "index.html")
    log_json(event="report", path=str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
