"""샘플 시연 한 번에 — 합성 데이터 만들기부터 정답 맞추기까지, 평소와 같은 명령을 차례로 부른다.

언제 쓰나: 처음 저장소를 받은 사람이 키 · 큰 내려받기 없이 (Docker Desktop 만 있으면)::

    python -m mdp.sample.demo                # 합성 Bronze → Silver → 검산 → Gold → 복사 → 확인
    python -m mdp.sample.demo --with-stream  # + Kafka 재생(빈 곳 · 중복) → 스트림 봉 → 대사

읽고 쓰는 것: ``data/sample/`` (합성 Bronze · 샘플 레이크) · 로컬 PostgreSQL (실행 기록 · 복사본 ·
대사, 종목 ``DEMOUSDT`` 로만) · Kafka 토픽 ``raw.demo.aggtrade`` (``--with-stream``). 진짜 레이크
``data/lake`` 와 진짜 원문 토픽은 건드리지 않는다.

[이유] 시연 전용 코드를 따로 두면 '시연은 되는데 진짜는 안 되는' 일이 생긴다. 그래서 이 파일은
       순서만 알고, 각 단계는 진짜 데이터에 쓰는 명령과 같다 — 다른 것은 레이크 위치(환경 변수)와
       종목뿐이다.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from mdp.sample.generate import STREAM_GAP, SYMBOL

__all__ = ["Step", "main", "plan_steps"]

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
SPARK_IMAGE: Final = "mdp-spark:4.0.1-delta4.0.1"
DEMO_TOPIC: Final = "raw.demo.aggtrade"
LOCAL_PG: Final = "postgresql://mdp:mdp_local_only@127.0.0.1:56432/mdp"
MONTH: Final = "2024-01"
MANIFEST_IN_CONTAINER: Final = "/lake/bronze/manifest.csv"


@dataclass(frozen=True, slots=True)
class Step:
    """[쓰임] 시연 한 단계 — 이름 · 무엇을 보여 주나 · 명령."""

    name: str
    why: str
    command: tuple[str, ...]


def _spark(*args: str) -> tuple[str, ...]:
    return ("docker", "compose", "run", "--rm", "spark", "python", "-m", *args)


def _host(*args: str) -> tuple[str, ...]:
    return (sys.executable, "-m", *args)


def plan_steps(root: Path, *, with_stream: bool, build_image: bool) -> list[Step]:
    """[쓰임] 돌릴 단계 목록 (순서대로)."""
    month = ("--symbol", SYMBOL, "--month", MONTH)
    steps = [
        Step(
            "PostgreSQL 켜기",
            "실행 기록 · 조회용 복사본 · 대사 결과",
            ("docker", "compose", "--profile", "db", "up", "-d", "--wait", "postgres"),
        )
    ]
    if build_image:
        steps.append(
            Step(
                "Spark 이미지 만들기",
                "처음 한 번 (몇 분)",
                ("docker", "compose", "--profile", "spark", "build", "spark"),
            )
        )
    steps += [
        Step(
            "합성 Bronze 만들기",
            "바이낸스와 같은 모양 zip · 체크섬 목록 · 정답지, 2 일째에 결함 5 종",
            _host("mdp.sample.generate", "--root", str(root)),
        ),
        Step(
            "Silver (Spark)",
            "체크섬 재대조 → 정제 · 격리 · 빈 곳 · 날 대조",
            _spark("mdp.silver.agg_trades", *month, "--manifest", MANIFEST_IN_CONTAINER),
        ),
        Step(
            "Silver 검산 (DuckDB)",
            "Spark 없이 같은 표를 다시 세어 봄",
            _host("mdp.query.check_silver", *month),
        ),
        Step(
            "Gold 1 분봉 (Spark)",
            "체결 → 1 분봉, 공식 봉과 대조",
            _spark("mdp.gold.bars_1m", *month, "--manifest", MANIFEST_IN_CONTAINER),
        ),
        Step(
            "PostgreSQL 복사", "한 트랜잭션 · 양쪽 SHA-256 대조", _host("mdp.serve.bars_1m", *month)
        ),
    ]
    if with_stream:
        gap_from, gap_to = (t.strftime("%Y-%m-%dT%H:%M:%S") for t in STREAM_GAP)
        steps += [
            Step(
                "Kafka 켜기",
                "원문 토픽용 브로커 한 대",
                ("docker", "compose", "--profile", "stream", "up", "-d", "--wait", "kafka"),
            ),
            Step(
                "Kafka 로 재생",
                f"1 일째를 보내되 {gap_from[11:16]} ~ {gap_to[11:16]} 은 안 보냄(PC 잠듦)"
                " · 97 번째마다 중복",
                _spark(
                    "mdp.stream.produce",
                    "--source",
                    "replay",
                    "--symbol",
                    SYMBOL,
                    "--start",
                    "2024-01-01T00:00",
                    "--end",
                    "2024-01-02T00:05",
                    "--topic",
                    DEMO_TOPIC,
                    "--fresh-topic",
                    "--duplicate-every",
                    "97",
                    "--gap-from",
                    gap_from,
                    "--gap-to",
                    gap_to,
                    "--manifest",
                    MANIFEST_IN_CONTAINER,
                ),
            ),
            Step(
                "스트림 1 분봉 (Structured Streaming)",
                "워터마크 · 중복 제거 · 확정된 분만",
                _spark(
                    "mdp.stream.bars_1m",
                    "--trigger",
                    "available-now",
                    "--fresh",
                    "--topic",
                    DEMO_TOPIC,
                    "--checkpoint-name",
                    "demo",
                ),
            ),
            Step(
                "매일 대사 · 메우기",
                "원인 나누기 → 배치 봉으로 메우기 → PostgreSQL",
                _spark(
                    "mdp.reconcile.daily",
                    "--symbol",
                    SYMBOL,
                    "--date",
                    "2024-01-01",
                    "--topic",
                    DEMO_TOPIC,
                    "--repair",
                ),
            ),
        ]
    steps.append(
        Step(
            "정답 맞추기 (DuckDB)",
            "넣은 결함을 빠짐없이 · 더하지도 않고 찾았나",
            _host("mdp.sample.verify", "--root", str(root)),
        )
    )
    return steps


def _image_exists() -> bool:
    found = subprocess.run(
        ["docker", "image", "inspect", SPARK_IMAGE], capture_output=True, check=False
    )
    return found.returncode == 0


def _clear(root: Path) -> None:
    """[쓰임] 이전 샘플을 지운다 — 저장소 ``data/`` 아래이고 정답지가 있는 폴더만.
    [이유] 엉뚱한 폴더(진짜 레이크 등)를 지우지 않도록 두 조건을 다 본다."""
    data_dir = (REPO_ROOT / "data").resolve()
    resolved = root.resolve()
    if not resolved.is_relative_to(data_dir) or resolved == data_dir:
        raise SystemExit(f"샘플 폴더는 {data_dir} 아래여야 한다: {resolved}")
    if resolved.exists():
        if not (resolved / "expected.json").exists():
            raise SystemExit(f"샘플 폴더가 아닌 것 같아 지우지 않는다(정답지 없음): {resolved}")
        shutil.rmtree(resolved)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 단계마다 무엇을 하는지 · 걸린 시간을 찍고, 실패하면 그 자리에서
    멈춘다."""
    parser = argparse.ArgumentParser(prog="python -m mdp.sample.demo")
    parser.add_argument("--root", type=Path, default=REPO_ROOT / "data" / "sample")
    parser.add_argument("--with-stream", action="store_true", help="Kafka 재생 · 스트림 · 대사까지")
    parser.add_argument("--keep", action="store_true", help="이전 샘플을 지우지 않고 이어서")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.keep:
        _clear(root)
    env = os.environ | {
        "MDP_LAKE_ROOT": str(root / "lake"),  # 호스트 명령 · compose 연결 모두 샘플 레이크로
        "MDP_BRONZE_ROOT": str(root / "bronze"),
        "MDP_PG_DSN": os.environ.get("MDP_PG_DSN", LOCAL_PG),
    }
    steps = plan_steps(root, with_stream=args.with_stream, build_image=not _image_exists())
    started = time.monotonic()
    for number, step in enumerate(steps, start=1):
        print(f"\n[{number}/{len(steps)}] {step.name} — {step.why}", flush=True)
        step_start = time.monotonic()
        result = subprocess.run(step.command, cwd=REPO_ROOT, env=env, check=False)
        print(
            f"      → {'끝' if result.returncode == 0 else '실패'}"
            f" ({time.monotonic() - step_start:.0f}초)",
            flush=True,
        )
        if result.returncode != 0:
            return result.returncode
    print(f"\n샘플 시연 끝 — {time.monotonic() - started:.0f}초", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
