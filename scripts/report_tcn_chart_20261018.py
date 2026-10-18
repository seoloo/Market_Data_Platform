"""Render frozen TCN result artifacts. Does not train or replay any strategy."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports/tcn_chart_20261018"
DOC_ASSETS = ROOT / "docs/research/assets/tcn_chart_20261018"


def main() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib import font_manager

    font = Path("C:/Windows/Fonts/malgun.ttf")
    if font.exists():
        font_manager.fontManager.addfont(str(font))
        plt.rcParams["font.family"] = "Malgun Gothic"
    plt.rcParams.update(
        {
            "axes.unicode_minus": False,
            "font.size": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.18,
        }
    )
    result = json.loads((OUT / "result.json").read_text(encoding="utf-8"))
    DOC_ASSETS.mkdir(parents=True, exist_ok=True)
    names = ("RULE", "LR", "TCN")
    colors = {"RULE": "#2764A5", "LR": "#CC741C", "TCN": "#176B55"}
    labels = {"RULE": "고정 지표 규칙", "LR": "로지스틱", "TCN": "TCN"}
    fig, axes = plt.subplots(2, 2, figsize=(15, 10), layout="constrained")
    for name in names:
        r = result["accounts"][f"holdout/{name}/base"]
        equity = np.r_[10000, np.asarray(r["daily_equity"])]
        days = [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=k) for k in range(len(equity))]
        axes[0, 0].plot(days, equity, label=labels[name], color=colors[name], linewidth=2)
        dd = (equity / np.maximum.accumulate(equity) - 1) * 100
        axes[0, 1].plot(days, dd, label=labels[name], color=colors[name], linewidth=1.7)
    for ax in axes[0]:
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.legend(frameon=False)
    axes[0, 0].set(title="역사확인 계좌 평가액 · 초기 10,000 USDT", ylabel="USDT")
    axes[0, 1].set(title="일말 기준 고점 대비 낙폭", ylabel="%")
    x = np.arange(3)
    width = 0.2
    for j, name in enumerate(names):
        r = result["classification"]["holdout"][name]["report"]
        values = [r[str(k)]["f1-score"] for k in (0, 1, 2)]
        bars = axes[1, 0].bar(
            x + (j - 1) * width, values, width, color=colors[name], label=labels[name]
        )
        axes[1, 0].bar_label(bars, fmt="%.2f", fontsize=9, padding=2)
    axes[1, 0].set(
        xticks=x,
        xticklabels=["관망", "롱", "숏"],
        ylim=(0, 1),
        title="클래스별 F1 · 확정 정답에 조건부",
        ylabel="F1",
    )
    axes[1, 0].legend(frameon=False)
    positions = np.arange(len(names))
    positive, negative = np.zeros(3), np.zeros(3)
    for key, label, color in (
        ("gross", "가격손익", "#2764A5"),
        ("fee", "수수료·준비금", "#C34949"),
        ("funding", "펀딩", "#E3A83D"),
    ):
        values = np.asarray([result["accounts"][f"holdout/{n}/base"][key] for n in names])
        if key == "fee":
            values = -values
        bottom = np.where(values >= 0, positive, negative)
        axes[1, 1].bar(positions, values, bottom=bottom, color=color, label=label)
        positive += np.maximum(values, 0)
        negative += np.minimum(values, 0)
    nets = [result["accounts"][f"holdout/{n}/base"]["net"] for n in names]
    axes[1, 1].scatter(positions, nets, color="#162C38", marker="D", zorder=4, label="순손익")
    for k, v in enumerate(nets):
        axes[1, 1].annotate(
            f"순 {v:+,.1f}",
            (k, v),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            fontsize=10,
        )
    axes[1, 1].axhline(0, color="#233743", linewidth=0.8)
    axes[1, 1].set(
        xticks=positions,
        xticklabels=[labels[n] for n in names],
        title="동일 계좌 순손익의 구성 · 가격불리는 가격손익에 포함",
        ylabel="USDT",
    )
    axes[1, 1].legend(frameon=False, fontsize=9)
    fig.suptitle("BTC·ETH 96봉 TCN · 2025-01~2026-08 후향 확인", fontsize=19)
    fig.savefig(OUT / "comparison.png", dpi=180)
    fig.savefig(DOC_ASSETS / "comparison.png", dpi=180)
    plt.close(fig)
    history = result["training"]["history"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8), layout="constrained")
    for key, label, color in (
        ("train_loss", "학습", "#2764A5"),
        ("validation_loss", "2024 검증", "#CC741C"),
    ):
        axes[0].plot(
            [r["epoch"] for r in history],
            [r[key] for r in history],
            marker="o",
            color=color,
            label=label,
        )
    axes[0].set(title="실제 학습 곡선", xlabel="Epoch", ylabel="Cross-entropy")
    axes[0].legend(frameon=False)
    axes[1].plot([0, 1], [0, 1], "--", color="#233743", label="일치선")
    for name in ("LR", "TCN"):
        bins = result["classification"]["holdout"][name]["calibration"]
        axes[1].plot(
            [b["confidence"] for b in bins],
            [b["accuracy"] for b in bins],
            marker="o",
            color=colors[name],
            label=labels[name],
        )
    axes[1].set(
        xlim=(0, 1),
        ylim=(0, 1),
        xlabel="가장 높은 클래스 점수의 평균",
        ylabel="그 클래스의 실제 정답 비율",
        title="역사확인 점수 검산 · 정답 확정 표본",
    )
    axes[1].legend(frameon=False)
    fig.savefig(OUT / "training_calibration.png", dpi=180)
    fig.savefig(DOC_ASSETS / "training_calibration.png", dpi=180)
    plt.close(fig)


if __name__ == "__main__":
    main()
