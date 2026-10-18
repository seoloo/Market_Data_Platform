# ruff: noqa: E501 -- Korean report tables contain long literal rows.
"""Frozen prepare/train/evaluate CLI. Never reads account keys or operating settings."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.tcn_chart_20261018 import account, data, dataset, metrics, models


def code_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def verify_registered() -> str:
    paths = [
        "docs/research/TCN_CHART_20261018_PREREG.md",
        "scripts/tcn_chart_20261018",
        "tests/test_tcn_chart_20261018.py",
    ]
    status = subprocess.check_output(["git", "status", "--porcelain", "--", *paths], text=True)
    if status.strip():
        raise RuntimeError("research source/design must be committed before training")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def report(result: dict[str, Any]) -> str:
    def pct(x: float) -> str:
        return f"{x * 100:+.2f}%"

    lines = [
        "# BTC·ETH 차트 TCN 실제 학습 결과",
        "",
        "제한된 후향 탐색. 미사용 OOS·정식 PASS·운영 승인 아님.",
        "",
        "## 실제 실행",
        "",
        f"- 장치: {result['training']['device']}, 파라미터 {result['training']['params']:,}개.",
        f"- 학습 {len(result['training']['history'])} epoch, {result['training']['training_seconds']:.1f}초.",
        "- 입력: BTC·ETH 공통 96개 4시간봉 × 10개 상대가격·거래량·지표.",
        "- TRAIN 2020-07~2023 / VALIDATION 2024 / 역사확인 2025-01~2026-08.",
        "- 학습은 모델 미학습 이후 구간과 분리했지만, 프로젝트가 이미 본 역사다.",
        "",
        "## 분류 성능",
        "",
        "| 구간 | 모델 | 정확도 | Macro F1 | Log-loss | Brier |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for split, group in result["classification"].items():
        for name, r in group.items():
            ll = f"{r['log_loss']:.4f}" if name != "RULE" else "확률모델 아님"
            br = f"{r['brier']:.4f}" if name != "RULE" else "—"
            lines.append(
                f"| {split} | {name} | {r['accuracy']:.3f} | {r['report']['macro avg']['f1-score']:.3f} | {ll} | {br} |"
            )
    lines += [
        "",
        "## 동일 위험 예산 계좌",
        "",
        "각 구간마다 10,000 USDT 시작. 명목10% 상한·1배, 동시에 한 포지션. 세전.",
        "",
        "| 구간 | 모델 | 순손익 USDT | CAGR | Sharpe | MDD | 거래 수 | fee | funding |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, r in result["accounts"].items():
        if not key.endswith("/base"):
            continue
        split, name, _ = key.split("/")
        lines.append(
            f"| {split} | {name} | {r['net']:+.2f} | {pct(r['cagr'])} | {r['sharpe']:.3f} | {-r['mdd'] * 100:.2f}% | {r['n_trades']} | {r['fee']:.2f} | {r['funding']:+.2f} |"
        )
    lines += [
        "",
        "## 역사확인 노출·거래 구조",
        "",
        "| 모델 | 평균/최대 노출 | 회전율 | 거래당 평균 bp | 평균 이익/손실 USDT | 부분진입 | 청산 지연 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("RULE", "LR", "TCN"):
        r = result["accounts"][f"holdout/{name}/base"]
        lines.append(
            f"| {name} | {r['exposure_mean'] * 100:.2f}% / {r['exposure_max'] * 100:.2f}% | {r['turnover_initial']:.2f} | {r['mean_trade_bps']:+.2f} | {r['mean_win']:+.2f} / {r['mean_loss']:+.2f} | {r['partial_entries']} | {r['delayed_exits']} |"
        )
    lines += [
        "",
        "## 역사확인 고정 민감도",
        "",
        "| 조건 | RULE 순손익 | LR 순손익 | TCN 순손익 |",
        "|---|---:|---:|---:|",
    ]
    for sens in ("base", "cost2", "delay5m", "half_volume", "worst_mark"):
        values = [
            result["accounts"][f"holdout/{name}/{sens}"]["net"] for name in ("RULE", "LR", "TCN")
        ]
        lines.append("| " + sens + " | " + " | ".join(f"{v:+.2f}" for v in values) + " |")
    lines += ["", "## 기여 분해", ""]
    for name in ("RULE", "LR", "TCN"):
        r = result["accounts"][f"holdout/{name}/base"]
        lines += [f"### {name}", "", "| 구분 | 건수 | 순손익 USDT |", "|---|---:|---:|"]
        for kind, choices in (("side", ("LONG", "SHORT")), ("symbol", data.SYMBOLS)):
            for choice in choices:
                selected = [t for t in r["trades"] if t[kind] == choice]
                lines.append(
                    f"| {choice} | {len(selected)} | {sum(t['net'] for t in selected):+.2f} |"
                )
        for year in (2025, 2026):
            selected = [
                t
                for t in r["trades"]
                if datetime.fromtimestamp(t["exit_ms"] / 1000, UTC).year == year
            ]
            lines.append(
                f"| {year} 종료 거래 | {len(selected)} | {sum(t['net'] for t in selected):+.2f} |"
            )
        top = sum(sorted((t["net"] for t in r["trades"]), reverse=True)[:5])
        lines += [
            "",
            f"최고5거래 손익 {top:+.2f}, 이를 뺀 거래합 {r['net'] - top:+.2f} USDT. 최악월 {pct(r['worst_month'])}.",
            f"거부 사유: `{json.dumps(r['veto'], ensure_ascii=False)}`.",
            "",
        ]
    lines += [
        "## 월별 증분 불확실성",
        "",
        "| 비교 | 월수 | 평균 차이 | HAC3 2SE 구간 | 앞/뒤 반쪽 |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, r in result["paired"].items():
        lines.append(
            f"| {name} | {r['months']} | {pct(r['mean'])} | [{pct(r['lower_2se'])}, {pct(r['upper_2se'])}] | {pct(r['first_half'])} / {pct(r['second_half'])} |"
        )
    lines += [
        "",
        "## 사전 고정 탐색 판정",
        "",
        f"**{result['verdict']['status']}**",
        "",
        *[f"- {k}: {v}" for k, v in result["verdict"]["checks"].items()],
        "",
        "이는 독립 수익 우위 인증이 아니다. 이미 본 역사·단일 학습seed·20개월 확인의 불확실성이 있다.",
        "",
        "## 확인하지 못한 것",
        "",
        "- 전체 코인 PIT 공통모델, 폐지 코인 포함 일반화, 주식 학습은 미실행.",
        "- 실제 호가·주문 큐·체결확률·역사적 규격 이력·mark 기반 청산 tier는 미확인.",
        "- 정산당 mark 일부는 hour시가 대리. 실제 rate와 지급시각은 사용했고 불리 mark 민감도를 별도 계산.",
        "- 24시간은 시간청산 시작. 지정가 전량 종료 지연과 미종결을 따로 기록한다.",
        "- 전체 G1/G5/G6/G7 운영 parity와 전향 OOS/Shadow/Paper/Live는 미실행.",
        "- 월별 HAC 구간도 적은 월수와 과거 연구 선택 오염을 제거하지 못한다.",
        "",
        "## 주식 확장",
        "",
        "시장공통 feature/label/model과 crypto execution/account를 분리했다. 주식에는 PIT·폐지,",
        "분할·배당·수정OHLC, 세션·휴장, 정지·가격제한, 공매도·대차, 분봉을 확보하고 별도 학습해야 한다.",
        "코인 모델 성적을 주식 수익 증거로 이전하지 않는다.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    import numpy as np

    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "prepare":
        data.prepare()
        return
    git = verify_registered()
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    data.prepare()
    stamp = {"git": git, "code_sha256": code_digest(), "started_utc": datetime.now(UTC).isoformat()}
    attempt = data.OUT / "attempt_v2.json"
    if attempt.exists():
        before = json.loads(attempt.read_text(encoding="utf-8"))
        if before["code_sha256"] != stamp["code_sha256"]:
            raise RuntimeError("code changed after attempt; audit required")
    else:
        data.save("attempt_v2.json", stamp)
    if (data.OUT / "result.json").exists():
        raise RuntimeError("completed evaluation cannot be repeated")
    cache = data.OUT / "dataset_v2.npz"
    if cache.exists():
        with np.load(cache, allow_pickle=False) as f:
            ds = dict(f)
    else:
        ds = dataset.build()
    aud = json.loads((data.OUT / "dataset_audit_v2.json").read_text(encoding="utf-8"))
    pred_file = data.OUT / "predictions.npz"
    if pred_file.exists():
        with np.load(pred_file, allow_pickle=False) as f:
            probs = dict(f)
        training = json.loads((data.OUT / "training.json").read_text(encoding="utf-8"))
    else:
        probs, training = models.train(ds["x"], ds["y"], ds["splits"], data.OUT)
        data.save("training.json", training)
        np.savez_compressed(pred_file, **probs)
    result: dict[str, Any] = {
        "manifest": stamp,
        "training": training,
        "classification": {},
        "accounts": {},
        "by_asset_year": {},
        "raw_direction_diagnostic": {},
        "dataset_audit": aud,
        "artifact_sha256": {
            name: hashlib.sha256((data.OUT / name).read_bytes()).hexdigest()
            for name in (
                "dataset_v2.npz",
                "data_manifest.json",
                "predictions.npz",
                "tcn.pt",
                "lr.joblib",
                "scaler.npz",
            )
        },
    }
    known = ds["y"] >= 0
    prior = np.bincount(ds["y"][(ds["splits"] == 0) & known], minlength=3)
    probs["CONSTANT"] = np.tile(prior / prior.sum(), (len(ds["y"]), 1))
    probs["RULE"] = np.eye(3)[ds["rules"]]
    for split, name in enumerate(("train", "validation", "holdout")):
        mask = (ds["splits"] == split) & known
        result["classification"][name] = {
            k: metrics.classification(ds["y"][mask], p[mask]) for k, p in probs.items()
        }
        result["raw_direction_diagnostic"][name] = {}
        for model, p in probs.items():
            predicted = metrics.decisions(p)
            observed = ds["future_return_24h"]
            active = (ds["splits"] == split) & (predicted != 0) & np.isfinite(observed)
            accuracy = (
                float(np.mean((predicted[active] == 1) == (observed[active] > 0)))
                if active.any()
                else None
            )
            result["raw_direction_diagnostic"][name][model] = {
                "active_signals": int(active.sum()),
                "direction_accuracy": accuracy,
                "meaning": "unstopped 24h direction, not after-cost trade outcome",
            }
    for sym in (0, 1):
        for year in (2025, 2026):
            years = np.asarray([datetime.fromtimestamp(t / 1000, UTC).year for t in ds["times"]])
            mask = (ds["symbols"] == sym) & (years == year) & (ds["splits"] == 2) & known
            result["by_asset_year"][f"{data.SYMBOLS[sym]}/{year}"] = {
                k: metrics.classification(ds["y"][mask], p[mask]) for k, p in probs.items()
            }
    assets = {s: data.load(s) for s in data.SYMBOLS}
    for split, period in enumerate(("train", "validation", "holdout")):
        sensitivities = (
            ("base", "cost2", "delay5m", "half_volume", "worst_mark") if split == 2 else ("base",)
        )
        for name in ("RULE", "LR", "TCN"):
            for sens in sensitivities:
                key = f"{period}/{name}/{sens}"
                print(json.dumps({"replay": key}), flush=True)
                r = account.replay(ds, metrics.decisions(probs[name]), split, assets, sens)
                result["accounts"][key] = r
    target = result["accounts"]["holdout/TCN/base"]
    bases = {k: result["accounts"][f"holdout/{k}/base"] for k in ("RULE", "LR")}
    result["paired"] = {
        f"TCN-{k}": metrics.paired(target["monthly"], r["monthly"]) for k, r in bases.items()
    }
    checks = {
        "TCN_positive": target["net"] > 0,
        "beats_both_net_sharpe": all(
            target["net"] > r["net"] and target["sharpe"] > r["sharpe"] for r in bases.values()
        ),
        "mdd_limit": target["mdd"] <= min(r["mdd"] for r in bases.values()) + 0.02,
        "both_halves": all(
            r["first_half"] > 0 and r["second_half"] > 0 for r in result["paired"].values()
        ),
        "all_sensitivities_positive": all(
            result["accounts"][f"holdout/TCN/{s}"]["net"] > 0
            for s in ("cost2", "delay5m", "half_volume", "worst_mark")
        ),
        "100_trades": target["n_trades"] >= 100,
        "no_unresolved": all(r["unresolved"] == 0 for r in result["accounts"].values()),
    }
    result["verdict"] = {
        "checks": checks,
        "status": "EXPLORATORY_FOLLOWUP_ONLY"
        if all(checks.values())
        else "NO_DEMONSTRATED_PRACTICAL_ADVANTAGE",
    }
    data.save("result.json", result)
    (data.OUT / "RESULTS.md").write_text(report(result), encoding="utf-8")
    print(json.dumps(result["verdict"]), flush=True)


if __name__ == "__main__":
    main()
