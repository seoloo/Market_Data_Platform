"""운영 상태 백업 (2026-10-04, 사용자 "그래 1번 진행해줘" — 상태 파일 백업).

``data/`` · ``reports/`` 는 git 에 올리지 않는다 → C: 디스크가 고장 나면 QS 실거래 상태(쌓은 현금·
QS 몫 TQQQ)·적립 진행·Paper 장부가 함께 사라진다. 매일 밤 다른 디스크로 복사한다.

사용 예::

    python -m scripts.backup_state run      # 오늘 백업
    python -m scripts.backup_state status   # 마지막 백업 보기

* **상태 묶음** ``state-YYYY-MM-DD.zip`` (수 MB) → F: 하드디스크에 저장,
  최근 ``KEEP`` 개만 남긴다. 들어가는 것: ``data/`` 의 작은 상태 파일, SQLite 장부(안전 복사),
  ``trading.db`` 에서 다시 받을 수 있는 시세 표를 뺀 장부 표, ``reports/`` (연구·실행 기록 제외).
* **큰 자료 거울** (첫 대상에만): 해시 고정 연구 자료·바이낸스 원자료·시세 캐시 — 바뀐 파일만 복사,
  지우지 않는다.
* 넣지 않는 것: ``.env``·토큰(비밀 — 다시 발급), ``trading.db`` 의 시세 표(다시 받음).
* 끝 코드: 0 성공 · 1 보조 대상 실패(대상을 더 둘 때) · 2 첫 대상 실패 — 21:20 점검이 실패를 알린다.

되살리기: 묶음을 저장소 루트에 풀면 경로가 그대로다. ``backup/trading_ledger.db`` 의 표는
``trading.db`` 에 옮겨 넣는다(문서 OPERATIONS §백업).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
TARGETS: tuple[Path, ...] = (Path("F:/AutoTrading_backup"),)
"""C: 와 다른 물리 디스크(F: 2TB 하드디스크). OneDrive 는 용량이 가득 차 올라가지 않아 뺐다
(2026-10-04 사용자) — 올라가지 않는 OneDrive 폴더는 C: 에 남을 뿐이라 백업이 아니다."""
KEEP = 30
MIRROR_DIRS = ("external", "research", "macro_sources")
"""``data/`` 아래 큰 자료 — 묶음에 넣지 않고 첫 대상에 거울로만."""
MARKET_TABLES = frozenset({"candles", "perp_metrics", "funding_rates"})
"""``trading.db`` 에서 다시 받을 수 있는 시세 표 (7.8 GB 대부분)."""
SKIP_REPORTS = ("research", "tasklog")
LAST = REPO / "reports" / "backup" / "last.json"


def _secret(name: str) -> bool:
    low = name.lower()
    return low.startswith(".env") or "token" in low or "secret" in low


def state_files(repo: Path) -> Iterator[tuple[Path, str]]:
    """(원본, 묶음 안 경로) — SQLite 파일은 빼고 따로 안전 복사한다."""
    data = repo / "data"
    for path in sorted(data.rglob("*")):
        rel = path.relative_to(data)
        if not path.is_file() or rel.parts[0] in MIRROR_DIRS or "bars" in rel.parts[:-1]:
            continue
        if path.suffix in {".db", ".log", ".lock"} or path.name.endswith((".db-wal", ".db-shm")):
            continue
        if _secret(path.name):
            continue
        yield path, f"data/{rel.as_posix()}"
    reports = repo / "reports"
    for path in sorted(reports.rglob("*")):
        rel = path.relative_to(reports)
        if path.suffix == ".lock" or rel.parts[0] in (*SKIP_REPORTS, "backup"):
            continue
        if path.is_file():
            yield path, f"reports/{rel.as_posix()}"


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def copy_sqlite(src: Path, dst: Path) -> None:
    """쓰는 중이어도 일관된 사본 (SQLite backup API)."""
    with closing(_ro(src)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)


def ledger_extract(src: Path, dst: Path) -> dict[str, int]:
    """``trading.db`` 에서 시세 표를 뺀 모든 표 → ``dst``. 표별 행 수."""
    counts: dict[str, int] = {}
    with closing(_ro(src)) as s, closing(sqlite3.connect(dst)) as d:
        rows = s.execute(
            "select name, sql from sqlite_master where type='table' and sql is not null"
            " and name not like 'sqlite_%'"
        ).fetchall()
        for name, sql in rows:
            if name in MARKET_TABLES:
                continue
            d.execute(sql)
            cur = s.execute(f'select * from "{name}"')
            width = len(cur.description)
            marks = ",".join("?" * width)
            n = 0
            while batch := cur.fetchmany(5000):
                d.executemany(f'insert into "{name}" values ({marks})', batch)
                n += len(batch)
            counts[name] = n
        d.commit()
    return counts


def build_zip(repo: Path, out: Path) -> dict[str, Any]:
    """상태 묶음 하나를 ``out`` 에 만든다. 안에 ``backup/manifest.json`` (파일별 SHA256)."""
    manifest: dict[str, str] = {}
    ledger: dict[str, int] = {}
    skipped: list[str] = []
    with (
        tempfile.TemporaryDirectory() as tmp,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf,
    ):

        def put(src: Path, arc: str) -> None:
            body = src.read_bytes()
            info = zipfile.ZipInfo.from_file(src, arc)
            zf.writestr(info, body, zipfile.ZIP_DEFLATED, 6)
            manifest[arc] = hashlib.sha256(body).hexdigest()

        for src, arc in state_files(repo):
            try:
                put(src, arc)
            except OSError:  # 다른 프로그램이 잠근 파일 — 건너뛰고 기록
                skipped.append(arc)
        for db in sorted((repo / "data").glob("*.db")):
            if db.name in {"trading.db", "market.db"}:
                continue
            copy = Path(tmp) / db.name
            copy_sqlite(db, copy)
            put(copy, f"data/{db.name}")
        trading = repo / "data" / "trading.db"
        if trading.exists():
            copy = Path(tmp) / "trading_ledger.db"
            ledger = ledger_extract(trading, copy)
            put(copy, "backup/trading_ledger.db")
        zf.writestr("backup/manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False))
    with zipfile.ZipFile(out) as zf:
        bad = zf.testzip()
    if bad is not None:
        raise RuntimeError(f"묶음 확인 실패: {bad}")
    return {
        "files": len(manifest),
        "bytes": out.stat().st_size,
        "ledger_rows": ledger,
        "skipped": skipped,
    }


def rotate(folder: Path, keep: int) -> list[str]:
    """``state-*.zip`` 중 오래된 것을 지워 ``keep`` 개만 남긴다 (백업 폴더 안에서만)."""
    zips = sorted(folder.glob("state-????-??-??.zip"))
    gone = zips[: max(0, len(zips) - keep)]
    for z in gone:
        z.unlink()
    return [z.name for z in gone]


def mirror(repo: Path, dest: Path) -> dict[str, int]:
    """큰 자료를 ``dest/mirror`` 에 — 없거나 크기·시각이 다른 파일만 복사, 지우지 않는다."""
    data = repo / "data"
    roots = [data / d for d in MIRROR_DIRS] + sorted(p for p in data.rglob("bars") if p.is_dir())
    copied = nbytes = seen = 0
    for root in roots:
        if not root.is_dir():
            continue
        for src in root.rglob("*"):
            if not src.is_file() or _secret(src.name):
                continue
            seen += 1
            dst = dest / "mirror" / "data" / src.relative_to(data)
            st = src.stat()
            if dst.exists():
                dt = dst.stat()
                if dt.st_size == st.st_size and int(dt.st_mtime) >= int(st.st_mtime):
                    continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            nbytes += st.st_size
    return {"files": seen, "copied": copied, "copied_bytes": nbytes}


@dataclass
class Outcome:
    when: str
    zip_name: str
    zip_info: dict[str, Any] = field(default_factory=dict)
    targets: dict[str, str] = field(default_factory=dict)
    mirror: dict[str, Any] = field(default_factory=dict)

    @property
    def code(self) -> int:
        states = list(self.targets.values())
        if not states or states[0] != "ok" or self.mirror.get("error"):
            return 2
        return 0 if all(s == "ok" for s in states) else 1


def run(repo: Path = REPO, targets: Sequence[Path] = TARGETS, *, keep: int = KEEP) -> Outcome:
    kst = datetime.now(UTC) + timedelta(hours=9)
    name = f"state-{kst:%Y-%m-%d}.zip"
    out = Outcome(when=f"{kst:%Y-%m-%d %H:%M} KST", zip_name=name)
    with tempfile.TemporaryDirectory() as tmp:
        made = Path(tmp) / name
        out.zip_info = build_zip(repo, made)
        for i, target in enumerate(targets):
            try:
                if i > 0 and not target.parent.exists():
                    raise FileNotFoundError(f"{target.parent} 없음")
                target.mkdir(parents=True, exist_ok=True)
                part = target / f"{name}.part"
                shutil.copyfile(made, part)
                part.replace(target / name)
                rotate(target, keep)
                out.targets[str(target)] = "ok"
            except OSError as exc:
                out.targets[str(target)] = f"실패: {exc}"
    first = Path(targets[0]) if targets else None
    if first is not None and out.targets.get(str(first)) == "ok":
        try:
            out.mirror = mirror(repo, first)
        except OSError as exc:
            out.mirror = {"error": str(exc)}
    return out


def _report(o: Outcome) -> str:
    mb = o.zip_info.get("bytes", 0) / 1e6
    lines = [f"백업 {o.when} · {o.zip_name} · 파일 {o.zip_info.get('files', 0)}개 · {mb:.1f} MB"]
    lines += [f"  {t}: {s}" for t, s in o.targets.items()]
    if o.zip_info.get("skipped"):
        lines.append(f"  잠겨서 건너뜀: {', '.join(o.zip_info['skipped'])}")
    if o.mirror:
        m = o.mirror
        if "error" in m:
            lines.append(f"  거울 실패: {m['error']}")
        else:
            gb = m["copied_bytes"] / 1e9
            lines.append(f"  거울: 파일 {m['files']}개 중 {m['copied']}개 복사 ({gb:.2f} GB)")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        o = run()
        LAST.parent.mkdir(parents=True, exist_ok=True)
        record = {**o.__dict__, "code": o.code}
        LAST.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(_report(o))
        return o.code
    if argv == ["status"]:
        print(LAST.read_text(encoding="utf-8") if LAST.exists() else "백업 기록 없음")
        return 0
    raise SystemExit("usage: python -m scripts.backup_state run | status")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
