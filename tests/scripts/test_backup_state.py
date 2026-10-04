from __future__ import annotations

import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

from scripts import backup_state as b


def _repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    files = {
        "data/toss/QS.json": "{}",
        "data/toss/bars/QQQ.csv": "x",
        "data/kis_token.json": "secret",
        "data/toss_token.json": "secret",
        "data/backfill.log": "log",
        "data/research/tradfi/a.csv": "fixed",
        "data/external/raw.zip": "raw",
        "reports/toss/2026-10-05.json": "{}",
        "reports/research/big.json": "{}",
        "reports/tasklog/x.log": "log",
        ".env": "TOSS_CLIENT_SECRET=x",
    }
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    with closing(sqlite3.connect(repo / "data" / "trading.db")) as c:
        c.execute("create table candles (t int)")
        c.execute("create table paper_days (d text, v real)")
        c.executemany("insert into paper_days values (?, ?)", [("a", 1.0), ("b", 2.0)])
        c.execute("insert into candles values (1)")
        c.commit()
    with closing(sqlite3.connect(repo / "data" / "demo_o2.db")) as c:
        c.execute("create table t (x int)")
        c.execute("insert into t values (7)")
        c.commit()
    return repo


def test_zip_keeps_state_and_drops_secrets_and_bulk(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    out = tmp_path / "s.zip"
    info = b.build_zip(repo, out)
    names = set(zipfile.ZipFile(out).namelist())
    assert "data/toss/QS.json" in names
    assert "reports/toss/2026-10-05.json" in names
    assert "data/demo_o2.db" in names and "backup/trading_ledger.db" in names
    for gone in (
        "data/kis_token.json",
        "data/toss_token.json",
        "data/toss/bars/QQQ.csv",
        "data/research/tradfi/a.csv",
        "data/external/raw.zip",
        "data/backfill.log",
        "data/trading.db",
        "reports/research/big.json",
        "reports/tasklog/x.log",
        ".env",
    ):
        assert gone not in names
    assert info["ledger_rows"] == {"paper_days": 2}
    manifest = json.loads(zipfile.ZipFile(out).read("backup/manifest.json"))
    assert set(manifest) == names - {"backup/manifest.json"}


def test_ledger_restores_rows(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dst = tmp_path / "l.db"
    b.ledger_extract(repo / "data" / "trading.db", dst)
    with closing(sqlite3.connect(dst)) as c:
        assert c.execute("select sum(v) from paper_days").fetchone()[0] == 3.0
        tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
    assert "candles" not in tables


def test_run_targets_rotation_and_mirror(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first, second = tmp_path / "F" / "bk", tmp_path / "missing_parent" / "bk"
    first.mkdir(parents=True)
    for i in range(3):
        (first / f"state-2020-01-0{i + 1}.zip").write_text("")
    o = b.run(repo, [first, second], keep=2)
    assert o.targets[str(first)] == "ok"
    assert o.targets[str(second)].startswith("실패")
    assert o.code == 1
    zips = sorted(p.name for p in first.glob("state-*.zip"))
    assert len(zips) == 2 and zips[-1] == o.zip_name
    assert (first / "mirror" / "data" / "research" / "tradfi" / "a.csv").exists()
    assert (first / "mirror" / "data" / "toss" / "bars" / "QQQ.csv").exists()
    assert not (first / "mirror" / "data" / "kis_token.json").exists()
    assert o.mirror["copied"] == 3
    again = b.run(repo, [first], keep=2)
    assert again.mirror["copied"] == 0 and again.code == 0


def test_first_target_failure_is_critical(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    blocker = tmp_path / "file"
    blocker.write_text("")
    o = b.run(repo, [blocker / "bk"])
    assert o.code == 2 and not o.mirror
