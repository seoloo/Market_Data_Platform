"""실적 개선형 주도주 경로 B — Open DART 로 유가증권시장 재무 자료를 받을 수 있나 (Phase 0, 조회만).

사용 예::

    python -m scripts.audit_dart_feasibility

1. ``corpCode.xml`` (전체 회사 고유번호) 한 번 → 141 자료의 유가증권시장 보통주 970 개
   (상장폐지 197 포함)와 잇기.
2. 고정 표본(141 종목군에 들어온 적 있는 현재 상장 12 · 상장폐지 12)에 대해 단일회사 전체 재무제표
   (``fnlttSinglAcntAll``) 사업보고서·1분기 → 131 의 세 측정치(매출 · 영업이익 · 영업활동
   현금흐름)가 있는지, 접수번호 앞 8 자리(접수일 = 당시 공개 시각)가 있는지.

Open DART 는 키를 주소의 ``crtfc_key`` 로만 받는다 — DART 에만 보내고, 주소·키를 출력하거나
파일에 남기지 않는다. 원응답은 ``data/research/fundamental_pilot/dart/`` (git 제외, 덮어쓰기
거부), 해시는 manifest. 수익 계산 없음.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import time
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
KR = REPO / "data" / "research" / "kr_stocks"
RAW = REPO / "data" / "research" / "fundamental_pilot" / "dart"
MANIFEST = REPO / "docs" / "research" / "fundamental-kr-dart-manifest.json"
API = "https://opendart.fss.or.kr/api"
REPORTS = {"11011": "사업보고서", "11013": "1분기"}
PER_GROUP = 12
PAUSE = 0.2

MEASURES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "revenue": (("ifrs-full_Revenue", "ifrs_Revenue"), ("매출액", "영업수익", "수익(매출액)")),
    "operating_income": (("dart_OperatingIncomeLoss",), ("영업이익", "영업이익(손실)")),
    "operating_cf": (
        (
            "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "ifrs_CashFlowsFromUsedInOperatingActivities",
        ),
        ("영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동으로 인한 현금흐름"),
    ),
}
"""131 의 세 측정치를 찾는 계정 ID(먼저)와 계정 이름(다음). 결과를 보기 전에 고정."""


def _norm(name: str) -> str:
    return re.sub(r"\s+", "", name)


def find_measures(rows: Sequence[Mapping[str, Any]]) -> dict[str, bool]:
    """재무제표 행에서 세 측정치가 있는지 (순수). 계정 ID 가 맞거나, 이름이 맞으면 있다."""
    out = {}
    for key, (ids, names) in MEASURES.items():
        wanted = {_norm(n) for n in names}
        out[key] = any(
            str(r.get("account_id", "")) in ids or _norm(str(r.get("account_nm", ""))) in wanted
            for r in rows
        )
    return out


def receipt_date(rows: Sequence[Mapping[str, Any]]) -> str | None:
    """접수번호(rcept_no) 앞 8 자리 = 접수일 (순수)."""
    for r in rows:
        no = str(r.get("rcept_no", ""))
        if len(no) >= 8 and no[:8].isdigit():
            return f"{no[:4]}-{no[4:6]}-{no[6:8]}"
    return None


def corp_map(xml_text: str) -> dict[str, str]:
    """CORPCODE.xml → {종목코드: 고유번호} (종목코드가 있는 회사만, 순수)."""
    out = {}
    for block in re.findall(r"<list>(.*?)</list>", xml_text, re.S):
        code = re.search(r"<corp_code>(.*?)</corp_code>", block)
        stock = re.search(r"<stock_code>(.*?)</stock_code>", block)
        if code and stock and stock.group(1).strip():
            out[stock.group(1).strip()] = code.group(1).strip()
    return out


def sample(
    ever: Sequence[str], info: Mapping[str, Mapping[str, str]], n: int = PER_GROUP
) -> list[str]:
    """141 종목군에 들어온 적 있는 종목에서 현재 상장 n · 상장폐지 n 을
    코드 순 고른 간격으로 (결정적)."""
    current = sorted(c for c in ever if info.get(c, {}).get("kind_listed"))
    gone = sorted(c for c in ever if not info.get(c, {}).get("kind_listed"))

    def pick(xs: list[str]) -> list[str]:
        if len(xs) <= n:
            return xs
        step = len(xs) / n
        return [xs[int(i * step)] for i in range(n)]

    return pick(current) + pick(gone)


def years_for(v: Mapping[str, str]) -> list[int]:
    """사업연도 둘: 2016 과 2023 중 그 회사가 상장해 있던 해.

    상장폐지면 폐지 전 마지막 온전한 해."""
    lstg = int((v.get("lstg") or "19000101")[:4])
    abol = v.get("abol") or ""
    last = int(abol[:4]) - 1 if abol else 2025
    picks = [y for y in (2016, 2023) if lstg <= y <= last]
    if not picks and last >= max(lstg, 2015):
        picks = [last]
    return picks


def _save(name: str, body: bytes) -> dict[str, Any]:
    path = RAW / name
    if path.exists():
        raise SystemExit(f"덮어쓰지 않는다: {path}")
    path.write_bytes(body)
    return {
        "file": f"data/research/fundamental_pilot/dart/{name}",
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def run() -> dict[str, Any]:
    import httpx

    from auto_trading.config.secrets import read_env_file

    key = read_env_file(REPO / ".env").get("OPENDART_API_KEY", "")
    if not key:
        raise SystemExit("OPENDART_API_KEY 가 .env 에 없다")
    if MANIFEST.exists():
        raise SystemExit(f"이미 실행했다: {MANIFEST}")
    RAW.mkdir(parents=True, exist_ok=True)
    info = json.loads((KR / "info.json").read_text(encoding="utf-8"))
    ever = json.loads((KR / "ever_universe.json").read_text(encoding="utf-8"))
    common = {c: v for c, v in info.items() if v.get("kind") == "101"}
    files: list[dict[str, Any]] = []
    fetched = datetime.now(UTC).isoformat(timespec="seconds")
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{API}/corpCode.xml", params={"crtfc_key": key})
        files.append(
            {**_save(f"corpcode-{fetched[:10]}.zip", resp.content), "http_status": resp.status_code}
        )
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml = zf.read(zf.namelist()[0]).decode("utf-8")
        mapping = corp_map(xml)
        listed_now = [c for c, v in common.items() if v.get("kind_listed")]
        delisted = [c for c, v in common.items() if not v.get("kind_listed")]
        coverage = {
            "corp_codes_with_stock_code": len(mapping),
            "kospi_common_listed": len(listed_now),
            "listed_linked": sum(1 for c in listed_now if c in mapping),
            "kospi_common_delisted": len(delisted),
            "delisted_linked": sum(1 for c in delisted if c in mapping),
            "ever_universe": len(ever),
            "ever_universe_linked": sum(1 for c in ever if c in mapping),
        }
        results = []
        for code in sample(ever, common):
            corp = mapping.get(code)
            v = common.get(code, {})
            row: dict[str, Any] = {
                "code": code,
                "name": v.get("name"),
                "delisted": not v.get("kind_listed"),
                "corp_code": corp,
                "checks": [],
            }
            if corp is None:
                results.append(row)
                continue
            for year in years_for(v):
                for rc, label in REPORTS.items():
                    got = None
                    for fs in ("CFS", "OFS"):  # 연결 먼저, 없으면 별도
                        r = client.get(
                            f"{API}/fnlttSinglAcntAll.json",
                            params={
                                "crtfc_key": key,
                                "corp_code": corp,
                                "bsns_year": str(year),
                                "reprt_code": rc,
                                "fs_div": fs,
                            },
                        )
                        name = f"fnltt-{code}-{year}-{rc}-{fs}.json"
                        files.append({**_save(name, r.content), "http_status": r.status_code})
                        time.sleep(PAUSE)
                        try:
                            body = r.json()
                        except ValueError:
                            body = {}
                        if body.get("status") == "000" and body.get("list"):
                            got = (fs, body["list"])
                            break
                    check: dict[str, Any] = {"year": year, "report": label}
                    if got is None:
                        check["status"] = "자료 없음"
                    else:
                        fs, rows = got
                        check.update(
                            {
                                "status": "있음",
                                "fs_div": fs,
                                "receipt_date": receipt_date(rows),
                                **find_measures(rows),
                            }
                        )
                    row["checks"].append(check)
            results.append(row)
    out = {
        "purpose": (
            "실적 개선형 주도주 경로 B Phase 0 — Open DART 재무 자료 확보 가능성"
            " (조회만, 수익 없음)"
        ),
        "fetched_at": fetched,
        "coverage": coverage,
        "sample": results,
        "files": files,
        "note": (
            "원응답은 git 제외 로컬 파일. 키는 DART 요청 주소에만 실었고 어디에도 남기지 않았다."
        ),
    }
    MANIFEST.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv:
        raise SystemExit("usage: python -m scripts.audit_dart_feasibility")
    out = run()
    sys.stdout.write(
        json.dumps({k: out[k] for k in ("coverage", "sample")}, ensure_ascii=False, indent=1) + "\n"
    )
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
