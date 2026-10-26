"""받아 둔 자료 들여오기 — 원본 저장소가 만든 파일을 고치지 않고 Bronze 로 복사한다(K-2 ①).

언제 쓰나: 한 번. 원본 연구 저장소(``Auto_Trading``)가 한국투자증권 · KIND 에서 받아 정리해 둔
          국내 주식 자료를 새로 받지 않고 쓰려 할 때.
읽고 쓰는 것:
- 읽기: ``--source`` 폴더의 파일 (실행 기록 ``*.log`` 는 빼고)
- 쓰기: Bronze ``<root>/imported/<이름>/`` (파일 그대로)과 ``_SUCCESS.json``
  (출처 · 파일마다 크기 · SHA-256), 체크섬 목록 ``manifests/bronze/imported.csv``

사용 예::

    python -m mdp.bronze.imported --source ../Auto_Trading/data/research/kr_stocks \
        --name auto_trading/research/kr_stocks

[이유] 이 파일들은 API 응답 원문이 아니라 원본 저장소의 받기 스크립트가 다시 정리해 쓴
       CSV · JSON 이다. 그래서 '원천 응답' 과 섞지 않고 ``imported/`` 아래에 두고, 출처(원본
       저장소 커밋)를 완료 표시에 적는다. 내용은 한 글자도 바꾸지 않는다 — 바꾸는 일은
       Silver 가 한다(ADR 0001).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from mdp.bronze.fetch import (
    REPO_ROOT,
    _file_sha256,
    _write_text_atomic,
    default_bronze_root,
    log_json,
    setup_cli_logging,
    write_manifest_entries,
)
from mdp.errors import BronzeConflictError, DataIntegrityError
from mdp.runlog.recorder import Artifact, NullRecorder, PostgresRecorder, RunRecorder, record_run

__all__ = ["DEFAULT_MANIFEST", "ImportResult", "ImportStatus", "import_folder", "main"]

DEFAULT_MANIFEST: Final = REPO_ROOT / "manifests" / "bronze" / "imported.csv"
SUCCESS: Final = "_SUCCESS.json"
SKIP_SUFFIXES: Final = (".log",)
"""원본 받기 스크립트의 실행 기록 — 자료가 아니라 들여오지 않는다."""


class ImportStatus(StrEnum):
    """[쓰임] 들여오기 결과."""

    COPIED = "copied"
    """새로 복사했다."""
    ALREADY_PRESENT = "already_present"
    """Bronze 에 이미 있고, 모든 파일이 완료 표시의 SHA-256 과 같다."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    """[쓰임] 들여오기 한 번의 결과."""

    status: ImportStatus
    files: int
    size_bytes: int
    rows: tuple[tuple[str, int, str], ...]
    """체크섬 목록에 쓸 (상대 경로, 크기, SHA-256)."""


def _source_files(source: Path) -> list[Path]:
    """[쓰임] 들여올 파일 — 하위 폴더까지, 실행 기록 빼고, 경로 순."""
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and not path.name.endswith(SKIP_SUFFIXES) and path.name != SUCCESS
    )


def _git_commit(folder: Path) -> str | None:
    """[쓰임] 원본 저장소의 지금 커밋(있으면) — 출처로 적는다."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout.strip() or None


def _rows(name: str, folder: Path) -> tuple[tuple[str, int, str], ...]:
    """[쓰임] 폴더 안 모든 파일의 (Bronze 상대 경로, 크기, SHA-256) — 완료 표시 포함."""
    rows = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        sha256, size_bytes = _file_sha256(path)
        rows.append((f"imported/{name}/{path.relative_to(folder).as_posix()}", size_bytes, sha256))
    return tuple(rows)


def import_folder(
    source: Path, bronze_root: Path, name: str, *, now: datetime | None = None
) -> ImportResult:
    """[쓰임] ``source`` 를 Bronze ``imported/<name>/`` 로 그대로 복사한다.
    이미 있으면 대조만 한다.
    [이유] ``.part`` 폴더에 다 복사하고 파일마다 원본과 SHA-256 을 맞춘 뒤 이름을 바꾼다
           — 중간에 끊겨도 반쯤 복사된 자료가 Bronze 에 보이지 않고, 복사 중 손상도 잡는다.
           이미 있는 것이 다르면 덮어쓰지 않고 멈춘다."""
    target = bronze_root / "imported" / Path(*name.split("/"))
    if (target / SUCCESS).exists():
        marker = json.loads((target / SUCCESS).read_text(encoding="utf-8"))
        for entry in marker["files"]:
            sha256, size_bytes = _file_sha256(target / Path(*entry["path"].split("/")))
            if (sha256, size_bytes) != (entry["sha256"], entry["size_bytes"]):
                raise BronzeConflictError(f"들여온 파일이 완료 표시와 다르다: {entry['path']}")
        rows = _rows(name, target)
        return ImportResult(
            ImportStatus.ALREADY_PRESENT, len(marker["files"]), marker["size_bytes"], rows
        )
    if target.exists():
        raise BronzeConflictError(f"완료 표시 없는 Bronze 폴더가 있다(사람이 확인): {target}")

    files = _source_files(source)
    if not files:
        raise DataIntegrityError(f"들여올 파일이 없다: {source}")
    part = target.with_name(target.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    try:
        entries: list[dict[str, Any]] = []
        for path in files:
            relative = path.relative_to(source).as_posix()
            destination = part / Path(*relative.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            source_sha256, source_size = _file_sha256(path)
            shutil.copyfile(path, destination)
            with destination.open("r+b") as stream:  # 쓰기로 열어야 Windows 에서 fsync 가 된다
                os.fsync(stream.fileno())
            if _file_sha256(destination) != (source_sha256, source_size):
                raise DataIntegrityError(f"복사한 파일이 원본과 다르다: {relative}")
            entries.append({"path": relative, "size_bytes": source_size, "sha256": source_sha256})
        marker = {
            "kind": "imported",
            "source": source.as_posix(),  # 준 그대로 — 개인 경로(홈 폴더)를 적지 않게
            "source_git_commit": _git_commit(source),
            "skipped_suffixes": list(SKIP_SUFFIXES),
            "copied_at": (now or datetime.now(UTC)).isoformat(),
            "size_bytes": sum(e["size_bytes"] for e in entries),
            "files": entries,
        }
        _write_text_atomic(
            part / SUCCESS, json.dumps(marker, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        part.replace(target)
    finally:
        shutil.rmtree(part, ignore_errors=True)
    return ImportResult(
        ImportStatus.COPIED, len(entries), marker["size_bytes"], _rows(name, target)
    )


def _build_parser() -> argparse.ArgumentParser:
    """[쓰임] 명령줄 인자 정의."""
    parser = argparse.ArgumentParser(
        prog="python -m mdp.bronze.imported", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--source", required=True, type=Path, help="들여올 폴더")
    parser.add_argument("--name", required=True, help="Bronze imported/ 아래 이름 (슬래시로)")
    parser.add_argument(
        "--root", type=Path, help="Bronze 위치 (기본: MDP_BRONZE_ROOT 또는 data/lake/bronze)"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--pg-dsn",
        default=os.environ.get("MDP_PG_DSN"),
        help="실행 기록 PostgreSQL 주소 (기본: MDP_PG_DSN, 없으면 기록 안 함)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 들여오고, 체크섬 목록 · 실행 기록을 남긴다."""
    setup_cli_logging()
    args = _build_parser().parse_args(argv)
    bronze_root: Path = args.root or default_bronze_root()
    params = {"source": str(args.source), "name": args.name, "bronze_root": str(bronze_root)}
    recorder: RunRecorder = PostgresRecorder(args.pg_dsn) if args.pg_dsn else NullRecorder()
    try:
        with record_run(recorder, "bronze.imported", params) as run:
            result = import_folder(args.source, bronze_root, args.name)
            marker_sha = next(sha for path, _, sha in result.rows if path.endswith(SUCCESS))
            run.add(
                Artifact(
                    path=f"imported/{args.name}",
                    layer="bronze",
                    status=result.status.value,
                    size_bytes=result.size_bytes,
                    sha256=marker_sha,
                    row_count=result.files,
                )
            )
    finally:
        if isinstance(recorder, PostgresRecorder):
            recorder.close()
    changed = write_manifest_entries(args.manifest, result.rows)
    log_json(
        event="imported",
        name=args.name,
        status=result.status.value,
        files=result.files,
        size_bytes=result.size_bytes,
        manifest_changed=changed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
