"""
Phase 2C: Baseline comparisons.

Compares the logistic regression model against naive baselines:
1. Seed-only baseline (historical win rate by seed matchup)
2. KenPom ranking baseline (higher-ranked team wins, prob by rank gap bucket)
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def load_predictions() -> pd.DataFrame:
    return pd.read_csv(ARTIFACTS_DIR / "loyo_predictions.csv")


def seed_baseline(df: pd.DataFrame) -> np.ndarray:
    """
    Seed-only baseline: P(team_a_wins) = historical win rate for that
    seed matchup, computed via LOYO to avoid leakage.
    """
    seasons = sorted(df["season"].unique())
    probs = pd.Series(index=df.index, dtype=float)

    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test_mask = df["season"] == holdout_season

        # Compute historical win rates by seed matchup from training seasons
        matchup_rates = (
            train.groupby(["team_a_seed", "team_b_seed"])["team_a_won"]
            .mean()
            .to_dict()
        )
        # Global fallback for unseen matchups
        global_rate = train["team_a_won"].mean()

        for idx in df[test_mask].index:
            key = (df.loc[idx, "team_a_seed"], df.loc[idx, "team_b_seed"])
            probs.loc[idx] = matchup_rates.get(key, global_rate)

    return probs.values


def kenpom_rank_baseline(df: pd.DataFrame) -> np.ndarray:
    """
    KenPom ranking baseline: probability based on who has the better
    KenPom rank. Uses LOYO to compute historical win rates by rank
    gap bucket.
    """
    # We need raw KenPom ranks — load from DB
    import duckdb
    conn = duckdb.connect(str(Path(__file__).parent.parent / "data" / "madness.duckdb"), read_only=True)
    full_df = conn.execute("SELECT * FROM staging.model_features").fetchdf()
    conn.close()

    full_df["rank_gap_bucket"] = pd.cut(
        full_df["team_b_kenpom_rank"] - full_df["team_a_kenpom_rank"],
        bins=[-200, -10, 0, 10, 25, 50, 100, 200],
        labels=["<-10", "-10-0", "0-10", "10-25", "25-50", "50-100", "100+"],
    )

    seasons = sorted(full_df["season"].unique())
    probs = pd.Series(index=full_df.index, dtype=float)

    for holdout_season in seasons:
        train = full_df[full_df["season"] != holdout_season]
        test_mask = full_df["season"] == holdout_season

        bucket_rates = train.groupby("rank_gap_bucket", observed=True)["team_a_won"].mean().to_dict()
        global_rate = train["team_a_won"].mean()

        for idx in full_df[test_mask].index:
            bucket = full_df.loc[idx, "rank_gap_bucket"]
            probs.loc[idx] = bucket_rates.get(bucket, global_rate)

    return probs.values


def main() -> None:
    df = load_predictions()
    y_true = df["team_a_won"].values
    y_model = df["predicted_prob"].values

    print("Generating baselines...\n")

    # Seed baseline
    y_seed = seed_baseline(df)

    # KenPom rank baseline
    y_rank = kenpom_rank_baseline(df)

    # Compare
    results = []
    for name, y_prob in [("Model (LR)", y_model), ("Seed-only", y_seed), ("KenPom rank", y_rank)]:
        brier = brier_score_loss(y_true, y_prob)
        ll = log_loss(y_true, y_prob)
        acc = ((y_prob >= 0.5) == y_true).mean()
        results.append({"method": name, "brier_score": brier, "log_loss": ll, "accuracy": acc})

    results_df = pd.DataFrame(results)
    print("Baseline Comparison:")
    print(results_df.to_string(index=False))
    print()

    # Improvement over seed baseline
    model_brier = results_df.loc[results_df["method"] == "Model (LR)", "brier_score"].values[0]
    seed_brier = results_df.loc[results_df["method"] == "Seed-only", "brier_score"].values[0]
    print(f"Model Brier improvement over seed baseline: {seed_brier - model_brier:.4f} "
          f"({(seed_brier - model_brier) / seed_brier:.1%} relative)")

    results_df.to_csv(ARTIFACTS_DIR / "baseline_comparison.csv", index=False)
    print("\nSaved baseline_comparison.csv")


if __name__ == "__main__":
    main()
