"""V2 1 회 실행: 정답 → 모델 → A 지표 → B 고정 정책 계좌(V1 실행기 그대로).

build/tcn-venv/Scripts/python.exe -m scripts.tcn_chart_v2_20261018.run

계정 키 · 운영 설정을 읽지 않는다. V1 산출물은 읽기만 한다.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.tcn_chart_20261018 import account, data, metrics
from scripts.tcn_chart_v2_20261018 import exec_audit, policy, stats, target

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/tcn_chart_v2_20261018"
V1 = ROOT / "reports/tcn_chart_20261018"
V1_DATASET_SHA = "5f1e5019b059acac1f6da2ff080ecdfa8a9e486789bdded6f50cbbc7f2b5ffca"
V1_MANIFEST_SHA = "789664f17c1987b9ead8523fb6e6b3c7a759c2149d48bf1431a7e4f4f64231f4"
REGISTERED = (
    "docs/research/TCN_CHART_20261018_V2_PREREG.md",
    "scripts/tcn_chart_v2_20261018",
    "tests/test_tcn_chart_v2_20261018.py",
)
PERIODS = ("train", "validation", "holdout")
SENS = ("cost2", "delay5m", "half_volume", "worst_mark")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def code_digest() -> str:
    h = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def registered() -> str:
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain", "--", *REGISTERED], cwd=ROOT, text=True
    )
    if dirty.strip():
        raise RuntimeError("V2 spec/code/tests must be committed before training")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def save(name: str, value: Any) -> None:
    (OUT / name).write_text(
        json.dumps(value, indent=1, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


def compact(r: dict[str, Any]) -> dict[str, Any]:
    keep = {k: v for k, v in r.items() if k not in ("trades", "daily_equity")}
    trades = r["trades"]

    def part(key: str, value: str) -> list[float]:
        sel = [t["net"] for t in trades if t[key] == value]
        return [len(sel), sum(sel)]

    keep["by_side"] = {s: part("side", s) for s in ("LONG", "SHORT")}
    keep["by_symbol"] = {s: part("symbol", s) for s in data.SYMBOLS}
    keep["top5_net"] = sum(sorted((t["net"] for t in trades), reverse=True)[:5])
    keep["stopped"] = sum(t["stopped"] for t in trades)
    return keep


def verdict_a(
    hold: dict[str, Any], tests: dict[str, Any], half: dict[str, Any], name: str
) -> dict[str, Any]:
    m = hold[name]
    checks = {
        "beats_zero_mean_ridge_mse": all(
            m["mse"] < hold[b]["mse"] for b in ("ZERO", "MEAN", "RIDGE") if b != name
        ),
        "nw_t_vs_zero_ge_2": tests[f"{name}_vs_ZERO"]["t"] >= 2.0,
        "nw_t_vs_mean_ge_2": tests[f"{name}_vs_MEAN"]["t"] >= 2.0,
        "halves_r2_positive": all(h[name]["r2_vs_mean"] > 0 for h in half.values()),
        "halves_ic_positive": all((h[name]["ic"] or 0) > 0 for h in half.values()),
    }
    if m["mse"] >= hold["MEAN"]["mse"] or m["mse"] >= hold["ZERO"]["mse"]:
        status = "불리"
    elif all(checks.values()):
        status = "예측 신호 있음(탐색)"
    else:
        status = "불명"
    return {"status": status, "checks": checks}


def main() -> None:
    import numpy as np

    git = registered()
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import torch
    from scripts.tcn_chart_v2_20261018 import models

    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    if torch.cuda.is_available():
        torch.cuda.set_per_process_memory_fraction(0.65)
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "result.json").exists():
        raise RuntimeError("V2 already evaluated; no rerun")
    stamp = {"git": git, "code_sha256": code_digest(), "started_utc": datetime.now(UTC).isoformat()}
    if (OUT / "attempt.json").exists():
        before = json.loads((OUT / "attempt.json").read_text(encoding="utf-8"))
        if before["code_sha256"] != stamp["code_sha256"]:
            raise RuntimeError("code changed after an attempt; audit required")
    save("attempt.json", stamp)

    # 자료 (V1 읽기 전용)
    if sha(V1 / "dataset_v2.npz") != V1_DATASET_SHA:
        raise RuntimeError("V1 dataset digest mismatch")
    if sha(V1 / "data_manifest.json") != V1_MANIFEST_SHA:
        raise RuntimeError("V1 manifest digest mismatch")
    keys = ("x", "times", "symbols", "splits", "rules", "future_return_24h")
    with np.load(V1 / "dataset_v2.npz", allow_pickle=False) as f:
        ds = {k: f[k] for k in keys}
    assets = {s: data.load(s) for s in data.SYMBOLS}
    y = target.targets(ds["times"], ds["symbols"], [assets[s]["h4"] for s in data.SYMBOLS])
    fr = ds["future_return_24h"]
    if not np.array_equal(np.isfinite(y), np.isfinite(fr)):
        raise RuntimeError("target availability differs from V1 future_return_24h")
    ok = np.isfinite(y)
    gap = float(np.max(np.abs(np.expm1(y[ok]) - fr[ok])))
    if gap > 1e-9:
        raise RuntimeError(f"target alignment mismatch {gap}")
    split, times, x = ds["splits"], ds["times"], ds["x"]
    mean, sd = x[split == 0].mean(axis=(0, 1)), x[split == 0].std(axis=(0, 1))
    sd = np.where(sd > 0, sd, 1)
    with np.load(V1 / "scaler.npz", allow_pickle=False) as f:
        diff = max(float(np.max(np.abs(f["mean"] - mean))), float(np.max(np.abs(f["sd"] - sd))))
    if diff > 1e-6:
        raise RuntimeError("scaler differs from V1")
    z = np.clip((x - mean) / sd, -8, 8).astype(np.float32)
    tr, va = (split == 0) & ok, (split == 1) & ok
    mu, sigma = float(y[tr].mean()), float(y[tr].std())
    zy = np.where(ok, (y - mu) / sigma, 0.0)

    preds: dict[str, Any] = {"ZERO": np.zeros(len(y)), "MEAN": np.full(len(y), mu)}
    _, rz = models.ridge(z, zy, tr)
    preds["RIDGE"] = mu + sigma * rz
    training: dict[str, Any] = {"target_mean": mu, "target_sd": sigma, "seeds": {}}
    cuda = torch.cuda.is_available()
    deadline = time.monotonic() + models.BUDGET_SEC["cuda" if cuda else "cpu"]
    for seed in (models.SEED, *models.STABILITY_SEEDS):
        try:
            zp, info = models.train_tcn(z, zy, tr, va, seed, OUT, deadline)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            zp, info = models.train_tcn(z, zy, tr, va, seed, OUT, deadline, batch=64)
            info["oom_restart"] = True
        name = "TCN" if seed == models.SEED else f"TCN_s{seed}"
        preds[name] = mu + sigma * zp
        training["seeds"][name] = info
        if time.monotonic() > deadline:
            training["budget_exhausted_after"] = name
            break
    training["env"] = {
        "torch": torch.__version__,
        "numpy": np.__version__,
        "gpu": torch.cuda.get_device_name(0) if cuda else None,
    }
    np.savez_compressed(OUT / "predictions.npz", y=y, **preds)

    # A — 예측
    names = list(preds)
    a: dict[str, Any] = {"periods": {}, "by_asset_year": {}}
    for k, period in enumerate(PERIODS):
        m = split == k
        a["periods"][period] = {n: stats.regression(y[m], preds[n][m], mu) for n in names}
    h = split == 2
    years = np.asarray([datetime.fromtimestamp(t / 1000, UTC).year for t in times])
    for s, sym in enumerate(data.SYMBOLS):
        for yr in (2025, 2026):
            m = h & (ds["symbols"] == s) & (years == yr)
            a["by_asset_year"][f"{sym}/{yr}"] = {
                n: stats.regression(y[m], preds[n][m], mu) for n in ("MEAN", "RIDGE", "TCN")
            }
    first, second = stats.halves(times[h])
    a["halves"] = {
        part: {n: stats.regression(y[h][mm], preds[n][h][mm], mu) for n in names}
        for part, mm in (("first", first), ("second", second))
    }
    a["nw_tests"] = {
        f"{model}_vs_{base}": stats.nw_t(
            stats.loss_diff_by_time(times[h], y[h], preds[base][h], preds[model][h])
        )
        for model in ("TCN", "RIDGE")
        for base in ("ZERO", "MEAN", "RIDGE", "TCN")
        if model != base
    }
    a["calibration_holdout"] = {n: stats.calibration(y[h], preds[n][h]) for n in ("RIDGE", "TCN")}
    hold = a["periods"]["holdout"]
    a["verdict"] = {n: verdict_a(hold, a["nw_tests"], a["halves"], n) for n in ("TCN", "RIDGE")}
    print(json.dumps({"A": a["verdict"]}, ensure_ascii=False), flush=True)

    # B — 고정 정책 + V1 실행기
    seeds = [n for n in names if n.startswith("TCN_s")]
    b: dict[str, Any] = {"tau": policy.TAU, "accounts": {}, "selection": {}}
    signals = {f"{n}-P": policy.decide(preds[n]) for n in ("TCN", "RIDGE", "MEAN", *seeds)}
    signals["RULE"] = ds["rules"]
    for n in ("TCN", "RIDGE"):
        signals[f"{n}-P0"] = policy.decide(preds[n], 0.0)
    for n in ("TCN", "RIDGE", "MEAN", *seeds):
        for tag, tau in (("P", policy.TAU), ("P0", 0.0)):
            b["selection"][f"{n}-{tag}"] = policy.selection(preds[n], y, h, tau)
    plan: list[tuple[str, int, str]] = []
    for k in range(3):
        plan += [(s, k, "base") for s in ("TCN-P", "RIDGE-P", "MEAN-P", "RULE")]
    plan += [(s, 2, sv) for s in ("TCN-P", "RIDGE-P", "RULE") for sv in SENS]
    plan += [(s, 2, "base") for s in ("TCN-P0", "RIDGE-P0", *[f"{n}-P" for n in seeds])]
    full: dict[str, Any] = {}
    for s, k, sv in plan:
        key = f"{PERIODS[k]}/{s}/{sv}"
        print(json.dumps({"replay": key}), flush=True)
        full[key] = account.replay(ds, signals[s], k, assets, sv)
        b["accounts"][key] = compact(full[key])
    tcn = full["holdout/TCN-P/base"]
    bases = {k: full[f"holdout/{k}/base"] for k in ("RULE", "RIDGE-P")}
    b["paired"] = {
        f"TCN-P-{k}": metrics.paired(tcn["monthly"], r["monthly"]) for k, r in bases.items()
    }
    checks = {
        "net_positive": tcn["net"] > 0,
        "beats_rule_ridge_net_sharpe": all(
            tcn["net"] > r["net"] and tcn["sharpe"] > r["sharpe"] for r in bases.values()
        ),
        "mdd_limit": tcn["mdd"] <= min(r["mdd"] for r in bases.values()) + 0.02,
        "both_halves": all(
            p["first_half"] > 0 and p["second_half"] > 0 for p in b["paired"].values()
        ),
        "sensitivities_positive": all(full[f"holdout/TCN-P/{sv}"]["net"] > 0 for sv in SENS),
        "100_trades": tcn["n_trades"] >= 100,
        "no_unresolved": all(
            full[f"holdout/TCN-P/{sv}"]["unresolved"] == 0 for sv in ("base", *SENS)
        ),
    }
    b["verdict"] = {
        "status": "후속 가치(탐색)" if all(checks.values()) else "실용 우위 미확인",
        "checks": checks,
    }
    b["execution_audit_holdout"] = exec_audit.count(
        times[h], ds["symbols"][h], assets, data.SYMBOLS
    )
    print(json.dumps({"B": b["verdict"]}, ensure_ascii=False), flush=True)

    result = {
        "doc": REGISTERED[0],
        "manifest": stamp,
        "completed_utc": datetime.now(UTC).isoformat(),
        "rows": {p: int((split == k).sum()) for k, p in enumerate(PERIODS)},
        "target_missing": {p: int(((split == k) & ~ok).sum()) for k, p in enumerate(PERIODS)},
        "training": training,
        "A": a,
        "B": b,
        "artifact_sha256": {
            p.name: sha(p) for p in sorted(OUT.glob("*")) if p.suffix in (".pt", ".npz")
        },
        "status": "EXPLORATORY_RETROSPECTIVE",
    }
    with (OUT / "result.json").open("x", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False, allow_nan=False)
    print(json.dumps({"done": True}), flush=True)


if __name__ == "__main__":
    main()
