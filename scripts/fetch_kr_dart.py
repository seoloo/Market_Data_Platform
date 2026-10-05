"""한국 실적 개선형 주도주 — Open DART 전체 자료 받기 (조회만, 이어 받기, 하루 한도 지킴).

사용 예::

    python -m scripts.fetch_kr_dart filings   # 회사별 정기공시 목록(모든 버전, last_reprt_at=N)
    python -m scripts.fetch_kr_dart fin       # 전체 재무제표 (CFS 먼저, 없을 때만 OFS)
    python -m scripts.fetch_kr_dart xbrl      # 정정 전 버전 XBRL (당시 값 복원용)
    python -m scripts.fetch_kr_dart status

* 대상: 141 자료의 유가증권시장 보통주 중 2015-01-01 ~ 2026-08-31 에 상장해 있던 회사 전부
  (지금 업종으로 미리 거르지 않는다 — 당시 비금융 여부는 확정 때 따로 판단,
  FUNDAMENTAL_KR_PIT_AUDIT §5).
* 원응답은 ``data/research/kr_dart/`` (git 제외). 이미 받은 파일은 건너뛴다(덮어쓰지 않음).
* 하루 요청 한도: DART 2 만 → ``DAILY_CAP`` 1 만 9 천에서 멈추고 다음 날 이어 간다.
  상태 ``020``(한도 초과)이면 바로 멈춘다.
* Open DART 는 키를 주소의 ``crtfc_key`` 로만 받는다 — DART 에만 보내고 출력·파일에 남기지 않는다.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
KR = REPO / "data" / "research" / "kr_stocks"
ROOT = REPO / "data" / "research" / "kr_dart"
API = "https://opendart.fss.or.kr/api"
FROM_DAY, TO_DAY = "20150101", "20260831"
YEARS = range(2015, 2027)
REPORT_CODES = ("11013", "11012", "11014", "11011")
"""1분기 · 반기 · 3분기 · 사업보고서."""
DAILY_CAP = 19_000
PAUSE = 0.12
OK, NO_DATA, OVER_LIMIT = "000", "013", "020"


# ------------------------------------------------------------------ 순수


def targets(info: Mapping[str, Mapping[str, str]]) -> list[str]:
    """2015-01-01 ~ 2026-08-31 에 상장해 있던 보통주 (지금 업종으로 거르지 않음)."""
    out = []
    for code, v in info.items():
        if v.get("kind") != "101":
            continue
        lstg = v.get("lstg") or "19000101"
        abol = v.get("abol") or ""
        if lstg <= TO_DAY and (not abol or abol >= FROM_DAY):
            out.append(code)
    return sorted(out)


def years_listed(v: Mapping[str, str]) -> list[int]:
    """사업연도 후보: 상장 연도 − 1 ~ 폐지 연도.

    결산월이 12 월이 아닌 회사의 사업연도 표기 차이를 덮으려고 한 해 앞부터 받는다."""
    lstg = int((v.get("lstg") or "19000101")[:4])
    abol = v.get("abol") or ""
    last = int(abol[:4]) if abol else max(YEARS)
    return [y for y in YEARS if lstg - 1 <= y <= last]


def report_versions(filings: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """정기공시 목록 → {보고서 이름(정정 표시 뗀 것): 접수번호 순 버전들}. 순수."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in filings:
        base = re.sub(r"^\[[^\]]+\]", "", str(f.get("report_nm", ""))).strip()
        groups[base].append(dict(f))
    return {k: sorted(v, key=lambda x: str(x.get("rcept_no", ""))) for k, v in groups.items()}


def reprt_code_for(base: str) -> str | None:
    """보고서 이름 → DART 보고서 코드.

    '분기보고서 (YYYY.MM)' 은 결산월 기준 첫/셋째 분기를 구분할 수 없어
    목록의 월을 보고 회사 결산월이 12 월일 때만 정한다(그 밖은 None)."""
    if base.startswith("사업보고서"):
        return "11011"
    if base.startswith("반기보고서"):
        return "11012"
    m = re.search(r"\((\d{4})\.(\d{2})\)", base)
    if base.startswith("분기보고서") and m:
        return {"03": "11013", "09": "11014"}.get(m.group(2))
    return None


def corrected(versions: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[tuple[str, str, str]]:
    """정정이 있었던 보고서의 (보고서 이름, 보고서 코드, 접수번호).

    원본과 중간 정정 모두 — 마지막 정정은 재무 API 가 준다."""
    out = []
    for base, vs in versions.items():
        code = reprt_code_for(base)
        if code is None or len(vs) < 2:
            continue
        for v in vs[:-1]:
            out.append((base, code, str(v.get("rcept_no"))))
    return out


class Budget:
    """하루 요청 수 (파일에 남김). 한도에 닿으면 ``spend`` 가 False."""

    def __init__(self, path: Path, cap: int = DAILY_CAP, today: str | None = None) -> None:
        self.path = path
        self.cap = cap
        self.today = today or datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005 — PC 날짜(KST)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.used = int(data.get(self.today, 0))
        self.data = data

    def spend(self) -> bool:
        if self.used >= self.cap:
            return False
        self.used += 1
        self.data[self.today] = self.used
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        return True


# ------------------------------------------------------------------ 받기


def _key() -> str:
    from auto_trading.config.secrets import read_env_file

    key = read_env_file(REPO / ".env").get("OPENDART_API_KEY", "")
    if not key:
        raise SystemExit("OPENDART_API_KEY 가 .env 에 없다")
    return key


def _corp_codes() -> dict[str, str]:
    """경로 B 감사에서 받은 corpCode zip 을 다시 읽는다(새 요청 없음)."""
    import io
    import zipfile

    from scripts.audit_dart_feasibility import corp_map

    zips = sorted(
        (REPO / "data" / "research" / "fundamental_pilot" / "dart").glob("corpcode-*.zip")
    )
    if not zips:
        raise SystemExit("corpCode zip 이 없다 — scripts.audit_dart_feasibility 먼저")
    with zipfile.ZipFile(io.BytesIO(zips[-1].read_bytes())) as zf:
        return corp_map(zf.read(zf.namelist()[0]).decode("utf-8"))


def _get(client: Any, budget: Budget, path: str, params: dict[str, str]) -> Any:
    if not budget.spend():
        raise StopIteration("하루 한도")
    resp = client.get(f"{API}/{path}", params=params)
    time.sleep(PAUSE)
    return resp


def _jobs(stage: str) -> Iterable[tuple[Path, str, dict[str, str]]]:
    info = json.loads((KR / "info.json").read_text(encoding="utf-8"))
    corps = _corp_codes()
    for code in targets(info):
        corp = corps.get(code)
        if corp is None:
            continue
        if stage == "filings":
            yield (
                ROOT / "filings" / f"{code}.json",
                "list.json",
                {
                    "corp_code": corp,
                    "bgn_de": FROM_DAY,
                    "end_de": TO_DAY,
                    "pblntf_ty": "A",
                    "last_reprt_at": "N",
                    "page_count": "100",
                },
            )
        elif stage == "fin":
            for year in years_listed(info[code]):
                for rc in REPORT_CODES:
                    yield (
                        ROOT / "fin" / code / f"{year}-{rc}-CFS.json",
                        "fnlttSinglAcntAll.json",
                        {
                            "corp_code": corp,
                            "bsns_year": str(year),
                            "reprt_code": rc,
                            "fs_div": "CFS",
                        },
                    )
        elif stage == "xbrl":
            listing = ROOT / "filings" / f"{code}.json"
            if not listing.exists():
                continue
            rows = json.loads(listing.read_text(encoding="utf-8")).get("list") or []
            for _base, rc, rcept in corrected(report_versions(rows)):
                yield (
                    ROOT / "xbrl" / code / f"{rcept}.zip",
                    "fnlttXbrl.xml",
                    {"rcept_no": rcept, "reprt_code": rc},
                )


def fetch(stage: str) -> dict[str, Any]:
    import httpx

    key = _key()
    budget = Budget(ROOT / "budget.json")
    got = skipped = fallback = 0
    stopped = ""
    with httpx.Client(timeout=60) as client:
        try:
            for path, endpoint, params in _jobs(stage):
                if path.exists():
                    skipped += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                resp = _get(client, budget, endpoint, {"crtfc_key": key, **params})
                body = resp.content
                if stage != "xbrl":
                    status = str(resp.json().get("status", "")) if body[:1] == b"{" else ""
                    if status == OVER_LIMIT:
                        stopped = "DART 한도 초과(020)"
                        break
                    if stage == "fin" and status == NO_DATA:
                        # 연결이 없을 때만 별도
                        ofs = path.with_name(path.name.replace("-CFS", "-OFS"))
                        if not ofs.exists():
                            r2 = _get(
                                client,
                                budget,
                                endpoint,
                                {"crtfc_key": key, **params, "fs_div": "OFS"},
                            )
                            ofs.write_bytes(r2.content)
                            fallback += 1
                if stage == "filings" and body[:1] == b"{":
                    first = resp.json()
                    pages = int(first.get("total_page") or 1)
                    rows = list(first.get("list") or [])
                    for page in range(2, pages + 1):  # 100 건이 넘으면 나머지 쪽
                        more = _get(
                            client,
                            budget,
                            endpoint,
                            {"crtfc_key": key, **params, "page_no": str(page)},
                        )
                        rows += more.json().get("list") or []
                    first["list"] = rows
                    body = json.dumps(first, ensure_ascii=False).encode("utf-8")
                path.write_bytes(body)
                got += 1
        except StopIteration as stop:
            stopped = str(stop)
    return {
        "stage": stage,
        "got": got,
        "skipped": skipped,
        "ofs_fallback": fallback,
        "used_today": budget.used,
        "stopped": stopped,
    }


def status() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for stage in ("filings", "fin", "xbrl"):
        folder = ROOT / stage
        out[stage] = sum(1 for _ in folder.rglob("*") if _.is_file()) if folder.exists() else 0
    budget = ROOT / "budget.json"
    out["budget"] = json.loads(budget.read_text(encoding="utf-8")) if budget.exists() else {}
    return out


def main(argv: list[str]) -> int:
    if argv not in (["filings"], ["fin"], ["xbrl"], ["status"]):
        raise SystemExit("usage: python -m scripts.fetch_kr_dart filings|fin|xbrl|status")
    out = status() if argv == ["status"] else fetch(argv[0])
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
