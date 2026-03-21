"""
Phase 2B: Calibration validation — the most important part of the project.

Generates calibration plots, Brier score decomposition, and additional metrics
from LOYO predictions.
"""

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def load_predictions() -> pd.DataFrame:
    return pd.read_csv(ARTIFACTS_DIR / "loyo_predictions.csv")


def calibration_plot(df: pd.DataFrame, n_bins: int = 10) -> None:
    """Plot predicted probability vs observed win rate in bins."""
    df = df.copy()
    df["bin"] = pd.cut(df["predicted_prob"], bins=n_bins, labels=False)

    cal = df.groupby("bin").agg(
        mean_predicted=("predicted_prob", "mean"),
        mean_observed=("team_a_won", "mean"),
        count=("team_a_won", "count"),
    ).dropna()

    fig, ax = plt.subplots(figsize=(7, 7))
    ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect calibration")
    ax.scatter(cal["mean_predicted"], cal["mean_observed"],
               s=cal["count"] * 3, zorder=5, label="Model")

    for _, row in cal.iterrows():
        ax.annotate(f"n={int(row['count'])}",
                     (row["mean_predicted"], row["mean_observed"]),
                     textcoords="offset points", xytext=(5, 5), fontsize=8)

    ax.set_xlabel("Predicted probability (team A wins)")
    ax.set_ylabel("Observed win rate")
    ax.set_title("Calibration Plot — LOYO Cross-Validation")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.legend()
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(ARTIFACTS_DIR / "calibration_plot.png", dpi=150)
    plt.close(fig)
    print(f"Saved calibration_plot.png")


def brier_decomposition(y_true: np.ndarray, y_prob: np.ndarray,
                        n_bins: int = 10) -> dict:
    """Murphy decomposition of Brier score into reliability, resolution, uncertainty."""
    base_rate = y_true.mean()
    uncertainty = base_rate * (1 - base_rate)

    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.digitize(y_prob, bins) - 1
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)

    reliability = 0.0
    resolution = 0.0
    n = len(y_true)

    for k in range(n_bins):
        mask = bin_indices == k
        nk = mask.sum()
        if nk == 0:
            continue
        ok = y_true[mask].mean()
        fk = y_prob[mask].mean()
        reliability += nk * (fk - ok) ** 2
        resolution += nk * (ok - base_rate) ** 2

    reliability /= n
    resolution /= n

    return {
        "brier_score": brier_score_loss(y_true, y_prob),
        "reliability": reliability,
        "resolution": resolution,
        "uncertainty": uncertainty,
    }


def accuracy_by_round(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy broken down by round."""
    df = df.copy()
    df["correct"] = ((df["predicted_prob"] >= 0.5) == df["team_a_won"]).astype(int)
    return df.groupby("round").agg(
        n_games=("correct", "count"),
        accuracy=("correct", "mean"),
    ).reset_index()


def accuracy_by_seed_matchup(df: pd.DataFrame) -> pd.DataFrame:
    """Accuracy broken down by seed matchup (e.g. 1v16, 2v15)."""
    df = df.copy()
    df["matchup"] = df["team_a_seed"].astype(int).astype(str) + "v" + df["team_b_seed"].astype(int).astype(str)
    df["correct"] = ((df["predicted_prob"] >= 0.5) == df["team_a_won"]).astype(int)
    return df.groupby("matchup").agg(
        n_games=("correct", "count"),
        accuracy=("correct", "mean"),
        mean_predicted=("predicted_prob", "mean"),
        actual_win_rate=("team_a_won", "mean"),
    ).reset_index().sort_values("matchup")


def main() -> None:
    df = load_predictions()
    y_true = df["team_a_won"].values
    y_prob = df["predicted_prob"].values

    print(f"Evaluating {len(df)} LOYO predictions\n")

    # Calibration plot
    calibration_plot(df)

    # Brier decomposition
    decomp = brier_decomposition(y_true, y_prob)
    print("Brier Score Decomposition:")
    print(f"  Brier score:  {decomp['brier_score']:.4f}")
    print(f"  Reliability:  {decomp['reliability']:.4f}  (lower is better, target < 0.02)")
    print(f"  Resolution:   {decomp['resolution']:.4f}  (higher is better)")
    print(f"  Uncertainty:  {decomp['uncertainty']:.4f}")
    print()

    # Additional metrics
    ll = log_loss(y_true, y_prob)
    auc = roc_auc_score(y_true, y_prob)
    print(f"Log-loss:  {ll:.4f}")
    print(f"AUC-ROC:   {auc:.4f}")
    print()

    # Accuracy by round
    round_acc = accuracy_by_round(df)
    print("Accuracy by round:")
    for _, row in round_acc.iterrows():
        print(f"  Round {int(row['round'])}: {row['accuracy']:.1%} ({int(row['n_games'])} games)")
    print()

    # Accuracy by seed matchup
    matchup_acc = accuracy_by_seed_matchup(df)
    print("Accuracy by seed matchup:")
    for _, row in matchup_acc.iterrows():
        print(f"  {row['matchup']:6s}: {row['accuracy']:.1%} predicted, "
              f"{row['actual_win_rate']:.1%} actual  (n={int(row['n_games'])})")

    # Save metrics summary
    metrics = {
        "brier_score": decomp["brier_score"],
        "reliability": decomp["reliability"],
        "resolution": decomp["resolution"],
        "uncertainty": decomp["uncertainty"],
        "log_loss": ll,
        "auc_roc": auc,
    }
    pd.DataFrame([metrics]).to_csv(ARTIFACTS_DIR / "metrics.csv", index=False)
    matchup_acc.to_csv(ARTIFACTS_DIR / "accuracy_by_matchup.csv", index=False)
    print("\nSaved metrics.csv and accuracy_by_matchup.csv")


if __name__ == "__main__":
    main()
