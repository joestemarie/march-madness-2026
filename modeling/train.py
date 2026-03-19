"""
Phase 2A: Model training with leave-one-year-out cross-validation.

Fits a logistic regression model to predict P(higher_seed_wins) using
KenPom-derived features from staging.model_features.

Default model: "round_interaction" (Experiment D winner) — includes
round indicator and seed_diff * round interaction term.
"""

import pickle
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
DB_PATH = Path(__file__).parent.parent / "data" / "madness.duckdb"

MVP_FEATURES = ["adj_em_diff", "seed_diff", "luck_diff"]

ROUND_IX_FEATURES = ["adj_em_diff", "seed_diff", "luck_diff", "round_ind", "seed_round_ix"]

FEATURE_SETS = {
    "mvp": MVP_FEATURES,
    "round_interaction": ROUND_IX_FEATURES,
}

DEFAULT_FEATURE_SET = "round_interaction"


def load_data() -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    df = conn.execute("SELECT * FROM staging.model_features").fetchdf()
    conn.close()
    return df


def _add_round_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["round_ind"] = (df["round"] == 2).astype(float)
    df["seed_round_ix"] = df["seed_diff"] * df["round_ind"]
    return df


def loyo_cv(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Leave-one-year-out cross-validation. Returns out-of-sample predictions."""
    seasons = sorted(df["season"].unique())
    results = []

    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test = df[df["season"] == holdout_season]

        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(l1_ratio=0, solver="lbfgs", max_iter=1000)),
        ])
        pipe.fit(train[features].values, train["team_a_won"].values)
        probs = pipe.predict_proba(test[features].values)[:, 1]

        season_results = test[
            ["season", "round", "team_a_canonical_name", "team_b_canonical_name",
             "team_a_seed", "team_b_seed", "team_a_won"]
        ].copy()
        season_results["predicted_prob"] = probs
        results.append(season_results)

    return pd.concat(results, ignore_index=True)


def fit_final_model(df: pd.DataFrame, features: list[str]) -> Pipeline:
    """Fit final model on all data."""
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("lr", LogisticRegression(l1_ratio=0, solver="lbfgs", max_iter=1000)),
    ])
    pipe.fit(df[features].values, df["team_a_won"].values)
    return pipe


def save_coefficients(pipe: Pipeline, features: list[str]) -> pd.DataFrame:
    lr = pipe.named_steps["lr"]
    scaler = pipe.named_steps["scaler"]
    coefs = lr.coef_[0]
    effective_coefs = coefs / scaler.scale_
    coef_df = pd.DataFrame({
        "feature": features,
        "coefficient_std": coefs,
        "coefficient_orig": effective_coefs,
        "scaler_mean": scaler.mean_,
        "scaler_scale": scaler.scale_,
    })
    coef_df.loc[len(coef_df)] = {
        "feature": "intercept",
        "coefficient_std": lr.intercept_[0],
        "coefficient_orig": lr.intercept_[0] - np.sum(coefs * scaler.mean_ / scaler.scale_),
        "scaler_mean": 0,
        "scaler_scale": 1,
    }
    return coef_df


def main(feature_set: str = DEFAULT_FEATURE_SET) -> None:
    if feature_set not in FEATURE_SETS:
        print(f"Unknown feature set '{feature_set}'. Choose from: {list(FEATURE_SETS.keys())}")
        return

    features = FEATURE_SETS[feature_set]
    print(f"Using {feature_set} feature set: {features}")

    df = load_data()
    needs_round = feature_set == "round_interaction"
    if needs_round:
        df = _add_round_features(df)
    print(f"Loaded {len(df)} rows, seasons {df['season'].min()}-{df['season'].max()}")

    # LOYO cross-validation
    predictions = loyo_cv(df, features)
    predictions.to_csv(ARTIFACTS_DIR / "loyo_predictions.csv", index=False)
    print(f"Saved LOYO predictions ({len(predictions)} rows)")

    # Final model on all data
    pipe = fit_final_model(df, features)
    with open(ARTIFACTS_DIR / "model.pkl", "wb") as f:
        pickle.dump({"pipeline": pipe, "feature_set": feature_set, "features": features}, f)
    print("Saved final model to model.pkl")

    # Coefficients
    coef_df = save_coefficients(pipe, features)
    coef_df.to_csv(ARTIFACTS_DIR / "coefficients.csv", index=False)
    print("\nFeature coefficients (standardized):")
    for _, row in coef_df.iterrows():
        print(f"  {row['feature']:25s}  {row['coefficient_std']:+.4f}")


if __name__ == "__main__":
    import sys
    feature_set = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FEATURE_SET
    main(feature_set)
