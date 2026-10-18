"""관찰 사실과 가설, 실제 행동의 진술을 구분하는 시점 계약."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research.deneb_watch.catalog import BY_ID, DAY_MS

Kind = Literal["fact", "hypothesis", "decision", "baseline", "failure"]
Action = Literal["pause_unspecified", "pause_new", "sell_existing", "resume", "none"]


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")
    kind: Kind
    factor_id: str = ""
    event_key: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2500)
    source_url: str = ""
    source_sha256: str = ""
    observed_ms: int | None = Field(default=None, ge=0)
    published_ms: int | None = Field(default=None, ge=0)
    value: Decimal | None = None
    unit: str = ""
    stance: Literal["unclassified", "risk", "support", "mixed"] = "unclassified"
    action: Action = "none"
    claimed_action_ms: int | None = Field(default=None, ge=0)
    reentry_plan: str = ""
    related_keys: tuple[str, ...] = ()
    details: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid(self) -> Record:
        if self.factor_id and self.factor_id not in BY_ID:
            raise ValueError("미등록 관찰 항목")
        if self.value is not None and not self.value.is_finite():
            raise ValueError("비유한 수치")
        if self.source_sha256 and (
            len(self.source_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.source_sha256)
        ):
            raise ValueError("원문 SHA256 형식")
        if self.kind == "fact" and not (self.factor_id and self.source_sha256 and self.source_url):
            raise ValueError("사실에는 관찰 항목·원문·출처 필요")
        expected_units = {
            "margin_debt": "USD_million",
            "margin_cash": "USD_million",
            "interest_burden": "ratio",
            "ai_loss": "USD_million",
            "vix": "index_points",
            "vvix": "index_points",
            "vol_term": "ratio",
            "real_yield": "percent",
            "repo": "percentage_points",
        }
        expected = expected_units.get(self.factor_id)
        if self.factor_id == "vol_term" and self.details.get("component") == "VIX9D":
            expected = "index_points"
        if self.factor_id == "repo" and self.details.get("component") in ("SOFR", "IORB"):
            expected = "percent"
        if self.kind == "fact" and expected and (self.value is None or self.unit != expected):
            raise ValueError("수치·단위 불일치")
        if self.kind != "decision" and self.action != "none":
            raise ValueError("행동 진술은 decision만 허용")
        return self


class Entry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    seq: int
    recorded_ms: int
    previous_sha256: str
    sha256: str
    record: Record


def canonical(record: Record) -> str:
    return json.dumps(
        record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def entry_digest(seq: int, recorded_ms: int, previous: str, payload: str) -> str:
    return hashlib.sha256(f"{seq}\n{recorded_ms}\n{previous}\n{payload}".encode()).hexdigest()


def visible(entry: Entry, as_of_ms: int) -> bool:
    dates = (entry.recorded_ms, entry.record.observed_ms, entry.record.published_ms)
    return all(value is None or value <= as_of_ms for value in dates)


def availability(entry: Entry | None, as_of_ms: int) -> str:
    if entry is None:
        return "MISSING"
    if not visible(entry, as_of_ms):
        return "NOT_AVAILABLE_AS_OF"
    if entry.record.kind == "failure":
        return "SOURCE_FAILED"
    if entry.record.kind != "fact":
        return "CONTEXT_ONLY"
    if entry.record.details.get("component"):
        return "PARTIAL"
    factor = BY_ID[entry.record.factor_id]
    age_from = entry.record.observed_ms
    if age_from is None:
        return "UNDATED_CONTEXT"
    return "STALE" if as_of_ms - age_from > factor.max_age_days * DAY_MS else "OBSERVED"


def decision_timing(entry: Entry) -> str:
    if entry.record.kind != "decision":
        raise DataIntegrityError("행동 진술 기록 아님")
    claimed = entry.record.claimed_action_ms
    if claimed is None:
        if entry.record.details.get("timing_origin") == "retrospective_user_report":
            return "RETROSPECTIVE_TIME_UNKNOWN"
        return "REPORTED_TIME_UNKNOWN"
    return "RETROSPECTIVE" if claimed < entry.recorded_ms else "CONTEMPORANEOUS"
