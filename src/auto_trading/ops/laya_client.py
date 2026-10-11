"""Laya 결정 모델 (공식 api.laya-ai.com) — 정해 둔 선택지 중 하나를 확률과 함께 고른다.

사용자 2026-10-11: console.laya-ai.com 에서 키 발급(``LAYA_API_KEY``),
영어 문장이 더 빠르고 정확하다.
값은 입력 100 만 토큰당 $0.05 (출력 무료), 충전 잔액은 서비스가 도는 동안 만료되지 않는다(이용약관).

* 한 요청에 문장 여러 개(``/v1/systemone/batch``, 최대 64 개) — 부르는 쪽은 개수를 신경 쓰지 않는다.
* 실패하면 그 자리를 None 으로 둔다(정보 기능이라 멈추지 않는다). 키는 출력하지 않는다.
* ``.env`` ``LAYA_OFF=true`` 면 쓰지 않는다. 주소는 ``LAYA_BASE_URL`` 로 바꿀 수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import read_env_file

__all__ = ["LAYA_URL", "Choice", "LayaClient", "choice_of", "from_env"]

LAYA_URL = "https://api.laya-ai.com"
BATCH = 64


@dataclass(frozen=True, slots=True)
class Choice:
    label: str
    prob: float


def choice_of(result: Mapping[str, Any] | None, question: str) -> Choice | None:
    """batch 결과 한 칸에서 질문 하나의 답 (고른 것, 그 확률)."""
    if not result:
        return None
    try:
        ans = result["answers"][question]
        label = str(ans["choice"])
        probs = ans.get("probabilities") or {}
        prob = float(probs.get(label, ans.get("answer_confidence", 0.0)))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return Choice(label, prob)


class LayaClient:
    __slots__ = ("_base", "_client", "_key", "last_ms", "tokens")

    def __init__(
        self, key: str, base: str = LAYA_URL, *, client: httpx.Client | None = None
    ) -> None:
        self._key, self._base = key, base.rstrip("/")
        self._client = client if client is not None else httpx.Client(timeout=30.0)
        self.last_ms = 0.0
        self.tokens = 0

    def __repr__(self) -> str:
        return "LayaClient(***)"

    def batch(
        self,
        states: Sequence[str],
        questions: Mapping[str, Any],
        model: str = "english",
    ) -> list[dict[str, Any] | None]:
        """문장마다 결과 한 칸 (실패 칸은 None). 64 개씩 나눠 보낸다."""
        out: list[dict[str, Any] | None] = []
        t0 = time.monotonic()
        for k in range(0, len(states), BATCH):
            chunk = list(states[k : k + BATCH])
            body = {"states": chunk, "questions": dict(questions), "model": model}
            try:
                r = self._client.post(
                    f"{self._base}/v1/systemone/batch",
                    json=body,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
                data = r.json() if r.status_code == 200 else {}
                results = list(data.get("results") or [])
                self.tokens += int((data.get("total_usage") or {}).get("input_tokens") or 0)
            except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                results = []
            out += [results[i] if i < len(results) else None for i in range(len(chunk))]
        self.last_ms = (time.monotonic() - t0) * 1000
        return out

    def close(self) -> None:
        self._client.close()


def from_env(root: Path) -> LayaClient | None:
    env = read_env_file(root / ".env")
    key = env.get("LAYA_API_KEY", "").strip()
    if not key or env.get("LAYA_OFF", "").strip().lower() == "true":
        return None
    return LayaClient(key, env.get("LAYA_BASE_URL", "").strip() or LAYA_URL)
