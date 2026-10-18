"""V3 1 회 실행: V2 + 매수 체결량 비율 · 펀딩비 채널.

B 는 실행기 E1(V1 그대로) · E2(바로 체결될 지정가 인정).

build/tcn-venv/Scripts/python.exe -m scripts.tcn_chart_v3_20261018.run

계정 키 · 운영 설정을 읽지 않는다. V1 · V2 산출물은 읽기만 한다.
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
from scripts.tcn_chart_v2_20261018 import policy, stats, target
from scripts.tcn_chart_v2_20261018.run import (
    V1_DATASET_SHA,
    V1_MANIFEST_SHA,
    compact,
    sha,
    verdict_a,
)
from scripts.tcn_chart_v3_20261018 import channels, exec_m, taker

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports/tcn_chart_v3_20261018"
V1 = ROOT / "reports/tcn_chart_20261018"
V2 = ROOT / "reports/tcn_chart_v2_20261018"
REGISTERED = (
    "docs/research/TCN_CHART_20261018_V3_PREREG.md",
    "scripts/tcn_chart_v3_20261018",
    "tests/test_tcn_chart_v3_20261018.py",
)
PERIODS = ("train", "validation", "holdout")
SENS = ("cost2", "delay5m", "half_volume", "worst_mark")


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
        raise RuntimeError("V3 spec/code/tests must be committed before training")
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def b_verdict(full: dict[str, Any], ex: str) -> dict[str, Any]:
    tcn = full[f"holdout/TCN3-P/base/{ex}"]
    bases = {k: full[f"holdout/{k}/base/{ex}"] for k in ("RULE", "RIDGE3-P")}
    paired = {f"TCN3-P-{k}": metrics.paired(tcn["monthly"], r["monthly"]) for k, r in bases.items()}
    sens = [full[f"holdout/TCN3-P/{sv}/{ex}"] for sv in SENS]
    checks = {
        "net_positive": tcn["net"] > 0,
        "beats_rule_ridge_net_sharpe": all(
            tcn["net"] > r["net"] and tcn["sharpe"] > r["sharpe"] for r in bases.values()
        ),
        "mdd_limit": tcn["mdd"] <= min(r["mdd"] for r in bases.values()) + 0.02,
        "both_halves": all(p["first_half"] > 0 and p["second_half"] > 0 for p in paired.values()),
        "sensitivities_positive": all(r["net"] > 0 for r in sens),
        "100_trades": tcn["n_trades"] >= 100,
        "no_unresolved": all(r["unresolved"] == 0 for r in (tcn, *sens)),
    }
    return {
        "status": "후속 가치(탐색)" if all(checks.values()) else "실용 우위 미확인",
        "checks": checks,
        "paired": paired,
    }


def main() -> None:
    import numpy as np

    git = registered()
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    import torch
    from scripts.tcn_chart_v3_20261018 import models

    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    cuda = torch.cuda.is_available()
    if cuda:
        torch.cuda.set_per_process_memory_fraction(0.65)
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "result.json").exists():
        raise RuntimeError("V3 already evaluated; no rerun")
    stamp = {"git": git, "code_sha256": code_digest(), "started_utc": datetime.now(UTC).isoformat()}
    if (OUT / "attempt.json").exists():
        before = json.loads((OUT / "attempt.json").read_text(encoding="utf-8"))
        if before["code_sha256"] != stamp["code_sha256"]:
            raise RuntimeError("code changed after an attempt; audit required")
    (OUT / "attempt.json").write_text(json.dumps(stamp, indent=1), encoding="utf-8")

    # 자료 (V1 · V2 읽기 전용)
    if sha(V1 / "dataset_v2.npz") != V1_DATASET_SHA:
        raise RuntimeError("V1 dataset digest mismatch")
    if sha(V1 / "data_manifest.json") != V1_MANIFEST_SHA:
        raise RuntimeError("V1 manifest digest mismatch")
    v2_result = json.loads((V2 / "result.json").read_text(encoding="utf-8"))
    if sha(V2 / "predictions.npz") != v2_result["artifact_sha256"]["predictions.npz"]:
        raise RuntimeError("V2 predictions digest mismatch")
    with np.load(V2 / "predictions.npz", allow_pickle=False) as f:
        v2p = {k: f[k] for k in ("y", "TCN", "RIDGE")}
    keys = ("x", "times", "symbols", "splits", "rules")
    with np.load(V1 / "dataset_v2.npz", allow_pickle=False) as f:
        ds = {k: f[k] for k in keys}
    manifest = json.loads((V1 / "data_manifest.json").read_text(encoding="utf-8"))
    assets = {s: data.load(s) for s in data.SYMBOLS}
    h4s = [assets[s]["h4"] for s in data.SYMBOLS]
    y = target.targets(ds["times"], ds["symbols"], h4s)
    ok = np.isfinite(y)
    if not np.array_equal(y[ok], v2p["y"][ok]) or not np.array_equal(ok, np.isfinite(v2p["y"])):
        raise RuntimeError("target differs from V2")
    per_symbol, channel_audit = [], {}
    for s in data.SYMBOLS:
        d = assets[s]
        taker4 = taker.taker_4h(s, d["minutes"], manifest, ROOT)
        imb = channels.taker_imbalance(taker4, d["h4"][:, 5])
        fund = channels.funding_last(d["h4"][:, 0] + channels.H4, d["funding"])
        per_symbol.append(np.column_stack((imb, fund)))
        channel_audit[s] = {
            "taker_imbalance_mean": float(np.nanmean(imb)),
            "taker_imbalance_sd": float(np.nanstd(imb)),
            "funding_mean": float(np.nanmean(fund)),
            "funding_sd": float(np.nanstd(fund)),
        }
    np.savez_compressed(OUT / "channels_4h.npz", BTCUSDT=per_symbol[0], ETHUSDT=per_symbol[1])
    ext = channels.extra(ds["times"], ds["symbols"], per_symbol, h4s)
    if not np.isfinite(ext).all():
        raise RuntimeError("new channel missing on a V2 row; no imputation")
    split, times = ds["splits"], ds["times"]
    x = np.concatenate((ds["x"].astype(np.float64), ext), axis=2)
    mean, sd = x[split == 0].mean(axis=(0, 1)), x[split == 0].std(axis=(0, 1))
    sd = np.where(sd > 0, sd, 1)
    z = np.clip((x - mean) / sd, -8, 8).astype(np.float32)
    tr, va = (split == 0) & ok, (split == 1) & ok
    mu, sigma = float(y[tr].mean()), float(y[tr].std())
    zy = np.where(ok, (y - mu) / sigma, 0.0)

    preds: dict[str, Any] = {"ZERO": np.zeros(len(y)), "MEAN": np.full(len(y), mu)}
    _, rz = models.ridge(z, zy, tr)
    preds["RIDGE3"] = mu + sigma * rz
    training: dict[str, Any] = {
        "target_mean": mu,
        "target_sd": sigma,
        "scaler_mean_new": mean[10:].tolist(),
        "scaler_sd_new": sd[10:].tolist(),
        "seeds": {},
    }
    deadline = time.monotonic() + models.BUDGET_SEC["cuda" if cuda else "cpu"]
    for seed in (models.SEED, *models.STABILITY_SEEDS):
        try:
            zp, info = models.train_tcn(z, zy, tr, va, seed, OUT, deadline)
        except torch.cuda.OutOfMemoryError:
            torch.cuda.empty_cache()
            zp, info = models.train_tcn(z, zy, tr, va, seed, OUT, deadline, batch=64)
            info["oom_restart"] = True
        name = "TCN3" if seed == models.SEED else f"TCN3_s{seed}"
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
    # 새 채널 기여 진단(보고만): 역사확인 입력의 두 채널을 정규화 0 으로
    device = "cuda" if cuda else "cpu"
    net = models.network().to(device)
    state = torch.load(OUT / f"tcn_seed{models.SEED}.pt", weights_only=True, map_location=device)
    net.load_state_dict(state)
    h = split == 2
    zh = z[h].copy()
    zh[:, :, 10:] = 0
    ablated = mu + sigma * models.predict(net, zh, device)
    np.savez_compressed(OUT / "predictions.npz", y=y, **preds)

    # A
    names = list(preds)
    a: dict[str, Any] = {"periods": {}, "by_asset_year": {}, "channels": channel_audit}
    for k, period in enumerate(PERIODS):
        m = split == k
        a["periods"][period] = {n: stats.regression(y[m], preds[n][m], mu) for n in names}
        a["periods"][period]["V2_TCN"] = stats.regression(y[m], v2p["TCN"][m], mu)
        a["periods"][period]["V2_RIDGE"] = stats.regression(y[m], v2p["RIDGE"][m], mu)
    a["ablation_holdout_new_channels_zeroed"] = stats.regression(y[h], ablated, mu)
    years = np.asarray([datetime.fromtimestamp(t / 1000, UTC).year for t in times])
    for s, sym in enumerate(data.SYMBOLS):
        for yr in (2025, 2026):
            m = h & (ds["symbols"] == s) & (years == yr)
            a["by_asset_year"][f"{sym}/{yr}"] = {
                n: stats.regression(y[m], preds[n][m], mu) for n in ("MEAN", "RIDGE3", "TCN3")
            }
    first, second = stats.halves(times[h])
    a["halves"] = {
        part: {n: stats.regression(y[h][mm], preds[n][h][mm], mu) for n in names}
        for part, mm in (("first", first), ("second", second))
    }
    every = {**preds, "V2_TCN": v2p["TCN"], "V2_RIDGE": v2p["RIDGE"]}
    a["nw_tests"] = {
        f"{model}_vs_{base}": stats.nw_t(
            stats.loss_diff_by_time(times[h], y[h], every[base][h], every[model][h])
        )
        for model in ("TCN3", "RIDGE3")
        for base in ("ZERO", "MEAN", "RIDGE3", "TCN3", "V2_TCN", "V2_RIDGE")
        if model != base
    }
    a["calibration_holdout"] = {n: stats.calibration(y[h], preds[n][h]) for n in ("RIDGE3", "TCN3")}
    hold = {**a["periods"]["holdout"], "RIDGE": a["periods"]["holdout"]["RIDGE3"]}
    tests = {
        "TCN3_vs_ZERO": a["nw_tests"]["TCN3_vs_ZERO"],
        "TCN3_vs_MEAN": a["nw_tests"]["TCN3_vs_MEAN"],
        "RIDGE_vs_ZERO": a["nw_tests"]["RIDGE3_vs_ZERO"],
        "RIDGE_vs_MEAN": a["nw_tests"]["RIDGE3_vs_MEAN"],
    }
    halves = {p: {**g, "RIDGE": g["RIDGE3"]} for p, g in a["halves"].items()}
    a["verdict"] = {
        "TCN3": verdict_a(hold, tests, halves, "TCN3"),
        "RIDGE3": verdict_a(hold, tests, halves, "RIDGE"),
    }
    print(json.dumps({"A": a["verdict"]}, ensure_ascii=False), flush=True)

    # B
    seeds = [n for n in names if n.startswith("TCN3_s")]
    signals = {f"{n}-P": policy.decide(preds[n]) for n in ("TCN3", "RIDGE3", *seeds)}
    signals["RULE"] = ds["rules"]
    signals["V2_TCN-P"] = policy.decide(v2p["TCN"])
    signals["V2_RIDGE-P"] = policy.decide(v2p["RIDGE"])
    for n in ("TCN3", "RIDGE3"):
        signals[f"{n}-P0"] = policy.decide(preds[n], 0.0)
    b: dict[str, Any] = {"tau": policy.TAU, "accounts": {}, "selection": {}}
    for n in ("TCN3", "RIDGE3", *seeds):
        for tag, tau in (("P", policy.TAU), ("P0", 0.0)):
            b["selection"][f"{n}-{tag}"] = policy.selection(preds[n], y, h, tau)
    plan: list[tuple[str, int, str, str]] = []
    for ex in ("E1", "E2"):
        for k in range(3):
            plan += [(s, k, "base", ex) for s in ("TCN3-P", "RIDGE3-P", "RULE")]
        plan += [(s, 2, sv, ex) for s in ("TCN3-P", "RIDGE3-P", "RULE") for sv in SENS]
        plan += [(s, 2, "base", ex) for s in ("TCN3-P0", "RIDGE3-P0", "V2_TCN-P", "V2_RIDGE-P")]
    plan += [(f"{n}-P", 2, "base", ex) for n in seeds for ex in ("E1", "E2")]
    full: dict[str, Any] = {}
    for s, k, sv, ex in plan:
        key = f"{PERIODS[k]}/{s}/{sv}/{ex}"
        print(json.dumps({"replay": key}), flush=True)
        if ex == "E2":
            with exec_m.e2():
                full[key] = account.replay(ds, signals[s], k, assets, sv)
        else:
            full[key] = account.replay(ds, signals[s], k, assets, sv)
        b["accounts"][key] = compact(full[key])
    b["verdict"] = {ex: b_verdict(full, ex) for ex in ("E1", "E2")}
    print(json.dumps({"B": {e: v["status"] for e, v in b["verdict"].items()}}, ensure_ascii=False))

    result = {
        "doc": REGISTERED[0],
        "manifest": stamp,
        "completed_utc": datetime.now(UTC).isoformat(),
        "rows": {p: int((split == k).sum()) for k, p in enumerate(PERIODS)},
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
