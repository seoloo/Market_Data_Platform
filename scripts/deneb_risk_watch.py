"""282 정보 전용: collect / report / note / attach-280 / verify. 주문·알림·예약 없음."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from auto_trading.core.errors import AutoTradingError, DataIntegrityError
from auto_trading.research.deneb_watch.domain import Record
from auto_trading.research.deneb_watch.journal import Journal
from auto_trading.research.deneb_watch.report import build, json_text, markdown
from auto_trading.research.deneb_watch.sources import (
    SOURCES,
    end_of_ny_day,
    fetch,
    parse,
    preserve_raw,
    utc_ms,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "deneb_watch_282"
REPORTS = REPO / "reports" / "deneb_watch_282"
BASELINE = REPO / "reports" / "deneb_shadow" / "latest.json"


def emit(payload: object) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def collect(journal: Journal, root: Path, *, source_id: str | None = None) -> list[dict[str, str]]:
    if source_id is not None and source_id not in {source.id for source in SOURCES}:
        raise DataIntegrityError("미등록 수집기")
    outcomes: list[dict[str, str]] = []
    with httpx.Client(timeout=20, headers={"User-Agent": "ORBIT-ReadOnly-Research/282"}) as client:
        for source in SOURCES:
            if source_id is not None and source.id != source_id:
                continue
            sha = ""
            try:
                body = fetch(source, client)
                captured_ms = utc_ms()
                sha = preserve_raw(root, body)
                records = parse(source, body, captured_ms=captured_ms)
                for record in records:
                    journal.append(record, recorded_ms=captured_ms)
                outcomes.append({"source": source.id, "status": "CAPTURED", "sha256": sha})
            except (httpx.HTTPError, AutoTradingError, UnicodeError, ValueError) as exc:
                captured_ms = utc_ms()
                error = type(exc).__name__
                for factor_id in source.factors:
                    journal.append(
                        Record(
                            key=f"failed:{source.id}:{factor_id}:{captured_ms}",
                            kind="failure",
                            event_key=f"source:{source.id}",
                            factor_id=factor_id,
                            summary=f"수집/파싱 실패: {error}",
                            source_url=source.url,
                            source_sha256=sha,
                        ),
                        recorded_ms=captured_ms,
                    )
                outcomes.append({"source": source.id, "status": error, "sha256": sha})
    derive_term(journal, root, recorded_ms=utc_ms())
    derive_repo(journal, root, recorded_ms=utc_ms())
    folder = root / "captures"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / f"{utc_ms()}.json").open("x", encoding="utf-8") as stream:
        json.dump(outcomes, stream, ensure_ascii=False, indent=2)
    return outcomes


def reparse(journal: Journal, root: Path, source_id: str, sha: str, *, recorded_ms: int) -> int:
    source = next((source for source in SOURCES if source.id == source_id), None)
    if source is None or not any(
        entry.record.source_sha256 == sha and entry.record.source_url == source.url
        for entry in journal.entries()
    ):
        raise DataIntegrityError("해당 출처의 보존 원문 기록 없음")
    verify_raw(journal, root)
    body = (root / "sources" / f"{sha}.bin").read_bytes()
    records = parse(source, body, captured_ms=recorded_ms)
    for record in records:
        journal.append(record, recorded_ms=recorded_ms)
    return len(records)


def derive_term(journal: Journal, root: Path, *, recorded_ms: int) -> bool:
    entries = journal.as_of(recorded_ms)
    vix = [
        entry
        for entry in entries
        if entry.record.factor_id == "vix" and entry.record.kind == "fact"
    ]
    short = [
        entry
        for entry in entries
        if entry.record.details.get("component") == "VIX9D" and entry.record.kind == "fact"
    ]
    if not vix or not short:
        return False
    a, b = vix[-1].record, short[-1].record
    if a.observed_ms != b.observed_ms or a.value is None or b.value is None or a.value <= 0:
        return False
    body = json.dumps(
        {
            "vix_key": a.key,
            "vix9d_key": b.key,
            "vix_sha256": a.source_sha256,
            "vix9d_sha256": b.source_sha256,
            "formula": "VIX9D/VIX",
            "value": str(b.value / a.value),
        },
        sort_keys=True,
    ).encode()
    sha = preserve_raw(root, body)
    journal.append(
        Record(
            key=f"282.1:term:{sha}",
            kind="fact",
            factor_id="vol_term",
            event_key=f"spx_vol:{a.observed_ms}",
            source_sha256=sha,
            source_url="https://www.cboe.com/tradable-products/vix/vix-historical-data",
            summary="동일 날짜 9일/30일 SPX 변동성 지수 비율. VIX 선물 곡선 아님",
            value=Decimal(b.value / a.value),
            unit="ratio",
            observed_ms=a.observed_ms,
            related_keys=(a.key, b.key),
            details={"derived": "VIX9D/VIX"},
        ),
        recorded_ms=recorded_ms,
    )
    return True


def derive_repo(journal: Journal, root: Path, *, recorded_ms: int) -> bool:
    entries = journal.as_of(recorded_ms)
    by_component = {
        component: {
            entry.record.observed_ms: entry.record
            for entry in entries
            if entry.record.kind == "fact" and entry.record.details.get("component") == component
        }
        for component in ("SOFR", "IORB")
    }
    common = set(by_component["SOFR"]) & set(by_component["IORB"]) - {None}
    if not common:
        return False
    observed = max(value for value in common if value is not None)
    a, b = by_component["SOFR"][observed], by_component["IORB"][observed]
    if a.value is None or b.value is None:
        return False
    value = a.value - b.value
    raw = json.dumps(
        {
            "formula": "SOFR-IORB",
            "sofr_key": a.key,
            "iorb_key": b.key,
            "observed_ms": observed,
            "value": str(value),
        },
        sort_keys=True,
    ).encode()
    sha = preserve_raw(root, raw)
    journal.append(
        Record(
            key=f"282.2:funding:{sha}",
            kind="fact",
            factor_id="repo",
            event_key=f"funding:{observed}",
            source_sha256=sha,
            source_url="https://fred.stlouisfed.org/series/SOFR",
            summary="동일 날짜 SOFR−IORB. 부호만으로 자금 경색을 단정하지 않음",
            observed_ms=observed,
            value=value,
            unit="percentage_points",
            related_keys=(a.key, b.key),
            details={"derived": "SOFR-IORB"},
        ),
        recorded_ms=recorded_ms,
    )
    return True


def attach_280(journal: Journal, root: Path, path: Path, *, recorded_ms: int) -> None:
    raw = path.read_bytes()
    payload: dict[str, Any] = json.loads(raw)
    generated = datetime.fromisoformat(str(payload["generated_utc"]))
    if generated.tzinfo is None:
        raise DataIntegrityError("280 보고서 시간대 미확인")
    last_day = date.fromisoformat(str(payload["last_day"]))
    details = {
        key: str(payload[key]) for key in ("start", "last_day", "paid_krw", "paid_usd", "value_usd")
    }
    sha = preserve_raw(root, raw)
    journal.append(
        Record(
            key=f"280:{sha}",
            kind="baseline",
            factor_id="price_reentry",
            event_key="baseline:280",
            summary="기존 280 보고서 읽기 전용 사본",
            source_url="local:reports/deneb_shadow/latest.json",
            source_sha256=sha,
            observed_ms=end_of_ny_day(last_day),
            published_ms=int(generated.timestamp() * 1000),
            details=details,
        ),
        recorded_ms=recorded_ms,
    )


def verify_raw(journal: Journal, root: Path) -> int:
    journal.verify()
    shas = {entry.record.source_sha256 for entry in journal.entries() if entry.record.source_sha256}
    for sha in shas:
        path = root / "sources" / f"{sha}.bin"
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise DataIntegrityError("원문 미확보/변조")
    return len(shas)


def main(argv: list[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("collect", "report", "note", "attach-280", "verify", "reparse-source")
    )
    parser.add_argument("--root", type=Path, default=DATA)
    parser.add_argument("--out", type=Path, default=REPORTS)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--as-of-ms", type=int)
    parser.add_argument("--source")
    parser.add_argument("--sha256")
    parser.add_argument(
        "--decision-now",
        action="store_true",
        help="과거 주장 시각 대신 지금부터의 새 판단으로 명시해 기록",
    )
    args = parser.parse_args(argv)
    journal = Journal(args.root / "watch.sqlite3")
    try:
        if args.command == "collect":
            emit(collect(journal, args.root, source_id=args.source))
        elif args.command == "reparse-source":
            if args.source is None or args.sha256 is None:
                parser.error("보존 원문 재해석에는 --source·--sha256 필요")
            count = reparse(journal, args.root, args.source, args.sha256, recorded_ms=utc_ms())
            emit({"reparsed_saved_source": args.source, "new_records": count})
        elif args.command == "note":
            if args.input is None:
                parser.error("note에는 --input JSON 필요")
            record = Record.model_validate_json(args.input.read_text(encoding="utf-8"))
            if record.kind not in ("decision", "hypothesis"):
                raise DataIntegrityError("수동 note는 행동 진술/가설만 허용")
            recorded_ms = utc_ms()
            if args.decision_now:
                if record.kind != "decision" or record.claimed_action_ms is not None:
                    raise DataIntegrityError("과거 시각이 없는 새 판단만 --decision-now 허용")
                old = [entry for entry in journal.entries() if entry.record.key == record.key]
                if old and old[0].record.model_copy(update={"claimed_action_ms": None}) == record:
                    record = old[0].record
                else:
                    record = record.model_copy(update={"claimed_action_ms": recorded_ms})
            entry = journal.append(record, recorded_ms=recorded_ms)
            emit(entry.model_dump(mode="json"))
        elif args.command == "attach-280":
            attach_280(journal, args.root, BASELINE, recorded_ms=utc_ms())
            emit({"status": "BASELINE_CONTEXT_ONLY"})
        elif args.command == "verify":
            emit(
                {
                    "raw_sources_verified": verify_raw(journal, args.root),
                    "journal_head_sha256": journal.verify(),
                }
            )
        else:
            verify_raw(journal, args.root)
            now_ms = utc_ms()
            as_of_ms = args.as_of_ms if args.as_of_ms is not None else now_ms
            if as_of_ms > now_ms:
                raise DataIntegrityError("미래 기준시각 보고서 거부")
            visible_entries = journal.as_of(as_of_ms)
            head = visible_entries[-1].sha256 if visible_entries else "0" * 64
            report = build(visible_entries, as_of_ms=as_of_ms, head_sha256=head)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / "latest.json").write_text(json_text(report), encoding="utf-8")
            (args.out / "latest.md").write_text(markdown(report), encoding="utf-8")
            emit(
                {
                    "status": "INFORMATION_ONLY",
                    "coverage": report["coverage"],
                    "comparison": report["comparison"]["status"],
                }
            )
    finally:
        journal.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
