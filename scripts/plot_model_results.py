"""Draw the Step 7 result charts from the latest model metrics file into docs/figures/.

    python scripts/plot_model_results.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from denguewatch.ml.features import FEATURE_GROUPS, FEATURE_LABELS  # noqa: E402

OUT = ROOT / "docs" / "figures"
TXT, TXT2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
BLUE, LIGHT_BLUE, ORANGE = "#2a78d6", "#9cc3ef", "#eb6834"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID,
                     "axes.labelcolor": TXT2, "xtick.color": TXT2, "ytick.color": TXT2,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "figure.facecolor": SURF, "axes.facecolor": SURF})


def title(ax, text):
    ax.set_title(text, loc="left", color=TXT, fontsize=11, fontweight="bold")


def main(metrics_path: Path | None = None) -> None:
    metrics_path = metrics_path or sorted((ROOT / "models").glob("*_metrics.json"))[-1]
    m = json.loads(Path(metrics_path).read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    base = m["test_base_rate"]

    # 1. model comparison
    comp = sorted(m["comparison"], key=lambda r: r["pr_auc"])
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    names = [r["model"] for r in comp]
    vals = [r["pr_auc"] for r in comp]
    ax.barh(names, vals, height=0.6, edgecolor=SURF, linewidth=2,
            color=[BLUE if n.startswith("DengueWatch") else LIGHT_BLUE for n in names])
    for i, v in enumerate(vals):
        ax.text(v + 0.003, i, f"{v:.3f}  ({v / base:.1f}×)", va="center", color=TXT, fontsize=9)
    ax.axvline(base, color=TXT2, lw=1, ls="--")
    ax.text(base + 0.002, len(vals) - 0.58, f"no-skill = {base:.3f}", color=TXT2, fontsize=8)
    ax.set_ylim(-0.5, len(vals) - 0.25)
    ax.set_xlim(0, max(vals) * 1.4)
    ax.set_xlabel("Precision–recall AUC on unseen test years")
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    title(ax, "Model comparison on unseen years")
    plt.tight_layout()
    plt.savefig(OUT / "model_comparison.png", dpi=200)
    plt.close()

    # 2. top features
    imp = list(m["feature_importance"].items())[:12][::-1]
    weather = set(FEATURE_GROUPS["weather"])
    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    ax.barh([FEATURE_LABELS.get(f, f) for f, _ in imp], [v for _, v in imp], height=0.6, edgecolor=SURF,
            linewidth=2, color=[BLUE if f in weather else ORANGE for f, _ in imp])
    ax.set_xlabel("Mean |SHAP value| (average effect on the risk score)")
    ax.grid(axis="x", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.legend(handles=[Patch(color=BLUE, label="Weather"), Patch(color=ORANGE, label="History / location / season")],
              frameon=False, loc="lower right", labelcolor=TXT2)
    title(ax, "What drives the risk score (top 12 features)")
    plt.tight_layout()
    plt.savefig(OUT / "feature_importance.png", dpi=200)
    plt.close()

    # 3. lead time
    w = m["warned"]
    a = [100 * w[f"medium_or_high_{k}plus_weeks"] for k in range(1, 5)]
    b = [100 * w[f"high_{k}plus_weeks"] for k in range(1, 5)]
    x, wd = np.arange(4), 0.36
    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    ax.bar(x - wd / 2 - 0.01, a, wd, color=BLUE, label="Medium or high alert", edgecolor=SURF, linewidth=2)
    ax.bar(x + wd / 2 + 0.01, b, wd, color=ORANGE, label="High alert only", edgecolor=SURF, linewidth=2)
    for i in range(4):
        ax.text(x[i] - wd / 2, a[i] + 1.5, f"{a[i]:.0f}%", ha="center", color=TXT, fontsize=9)
        ax.text(x[i] + wd / 2, b[i] + 1.5, f"{b[i]:.0f}%", ha="center", color=TXT, fontsize=9)
    ax.set_xticks(x, [f"≥{k} week{'s' if k > 1 else ''} ahead" for k in range(1, 5)])
    ax.set_ylabel("% of outbreaks warned")
    ax.set_ylim(0, 100)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, labelcolor=TXT2)
    title(ax, f"How early were outbreaks flagged? ({m['outbreak_weeks_in_test']} outbreak district-weeks)")
    plt.tight_layout()
    plt.savefig(OUT / "lead_time.png", dpi=200)
    plt.close()
    print(f"Charts written to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else None)
