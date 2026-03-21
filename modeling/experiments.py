"""
Phase 2 Experiments: Systematic model comparison with LOYO cross-validation.

Experiments A-D: Logistic regression variants
Experiments E-F: Non-logistic models (Random Forest, Gradient Boosting)

All experiments use leave-one-year-out CV and report:
Brier score, reliability, log-loss, AUC, accuracy.
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
DB_PATH = Path(__file__).parent.parent / "data" / "madness.duckdb"

MVP_FEATURES = ["adj_em_diff", "seed_diff", "luck_diff"]


def load_data() -> pd.DataFrame:
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    df = conn.execute("SELECT * FROM staging.model_features").fetchdf()
    conn.close()
    return df


def brier_decomposition(y_true: np.ndarray, y_prob: np.ndarray,
                        n_bins: int = 10) -> dict:
    """Murphy decomposition of Brier score."""
    base_rate = y_true.mean()
    uncertainty = base_rate * (1 - base_rate)
    bins = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.clip(np.digitize(y_prob, bins) - 1, 0, n_bins - 1)
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
    return {"reliability": reliability / n, "resolution": resolution / n,
            "uncertainty": uncertainty}


def compute_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    """Compute all evaluation metrics."""
    decomp = brier_decomposition(y_true, y_prob)
    return {
        "brier": brier_score_loss(y_true, y_prob),
        "reliability": decomp["reliability"],
        "log_loss": log_loss(y_true, y_prob),
        "auc": roc_auc_score(y_true, y_prob),
        "accuracy": ((y_prob >= 0.5) == y_true).mean(),
    }


def seed_baseline_loyo(df: pd.DataFrame) -> np.ndarray:
    """Seed-only baseline with LOYO to avoid leakage."""
    seasons = sorted(df["season"].unique())
    probs = pd.Series(index=df.index, dtype=float)
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test_mask = df["season"] == holdout_season
        matchup_rates = (
            train.groupby(["team_a_seed", "team_b_seed"])["team_a_won"]
            .mean().to_dict()
        )
        global_rate = train["team_a_won"].mean()
        for idx in df[test_mask].index:
            key = (df.loc[idx, "team_a_seed"], df.loc[idx, "team_b_seed"])
            probs.loc[idx] = matchup_rates.get(key, global_rate)
    return probs.values


def base_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return the standard columns for prediction output."""
    return df[["season", "round", "team_a_canonical_name", "team_b_canonical_name",
               "team_a_seed", "team_b_seed", "team_a_won"]].copy()


# ── MVP Baseline ──────────────────────────────────────────────────────────────

def run_mvp(df: pd.DataFrame) -> pd.DataFrame:
    """MVP logistic regression: adj_em_diff, seed_diff, luck_diff."""
    features = MVP_FEATURES
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
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment A: KenPom-only (no seed) ──────────────────────────────────────

def run_exp_a(df: pd.DataFrame) -> pd.DataFrame:
    """KenPom-only: adj_em_diff, luck_diff (no seed)."""
    features = ["adj_em_diff", "luck_diff"]
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
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment B: Seed-residualized AdjEM ────────────────────────────────────

def run_exp_b(df: pd.DataFrame) -> pd.DataFrame:
    """Seed-residualized AdjEM: residual + seed_diff + luck_diff."""
    seasons = sorted(df["season"].unique())
    results = []
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season].copy()
        test = df[df["season"] == holdout_season].copy()

        # Compute expected adj_em_diff by seed matchup from training data
        expected_em = (
            train.groupby(["team_a_seed", "team_b_seed"])["adj_em_diff"]
            .mean().to_dict()
        )
        global_em = train["adj_em_diff"].mean()

        # Compute residuals for train
        train["expected_em"] = train.apply(
            lambda r: expected_em.get((r["team_a_seed"], r["team_b_seed"]), global_em),
            axis=1
        )
        train["em_residual"] = train["adj_em_diff"] - train["expected_em"]

        # Compute residuals for test (using training-derived expectations)
        test["expected_em"] = test.apply(
            lambda r: expected_em.get((r["team_a_seed"], r["team_b_seed"]), global_em),
            axis=1
        )
        test["em_residual"] = test["adj_em_diff"] - test["expected_em"]

        features = ["em_residual", "seed_diff", "luck_diff"]
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(l1_ratio=0, solver="lbfgs", max_iter=1000)),
        ])
        pipe.fit(train[features].values, train["team_a_won"].values)
        probs = pipe.predict_proba(test[features].values)[:, 1]
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment C: Tuned regularization ───────────────────────────────────────

def run_exp_c(df: pd.DataFrame) -> pd.DataFrame:
    """Tuned L2 regularization via LogisticRegressionCV with LOYO folds."""
    features = MVP_FEATURES
    seasons = sorted(df["season"].unique())
    results = []
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test = df[df["season"] == holdout_season]

        X_train = train[features].values
        y_train = train["team_a_won"].values

        # Build LOYO fold indices within training data for inner CV
        train_seasons = sorted(train["season"].unique())
        cv_folds = []
        for inner_holdout in train_seasons:
            train_idx = np.where(train["season"].values != inner_holdout)[0]
            val_idx = np.where(train["season"].values == inner_holdout)[0]
            cv_folds.append((train_idx, val_idx))

        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)

        lr_cv = LogisticRegressionCV(
            Cs=10,
            cv=cv_folds,
            penalty="l2",
            solver="lbfgs",
            max_iter=1000,
            scoring="neg_brier_score",
        )
        lr_cv.fit(X_train_scaled, y_train)

        X_test_scaled = scaler.transform(test[features].values)
        probs = lr_cv.predict_proba(X_test_scaled)[:, 1]
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment D: Round interaction ──────────────────────────────────────────

def run_exp_d(df: pd.DataFrame) -> pd.DataFrame:
    """Round interaction: add round indicator and seed_diff * round."""
    seasons = sorted(df["season"].unique())
    results = []
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season].copy()
        test = df[df["season"] == holdout_season].copy()

        # Binary round indicator (0 for R1, 1 for R2)
        train["round_ind"] = (train["round"] == 2).astype(float)
        test["round_ind"] = (test["round"] == 2).astype(float)
        train["seed_round_ix"] = train["seed_diff"] * train["round_ind"]
        test["seed_round_ix"] = test["seed_diff"] * test["round_ind"]

        features = ["adj_em_diff", "seed_diff", "luck_diff", "round_ind", "seed_round_ix"]
        pipe = Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(l1_ratio=0, solver="lbfgs", max_iter=1000)),
        ])
        pipe.fit(train[features].values, train["team_a_won"].values)
        probs = pipe.predict_proba(test[features].values)[:, 1]
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment E: Random Forest (calibrated) ─────────────────────────────────

def run_exp_e(df: pd.DataFrame) -> pd.DataFrame:
    """Calibrated Random Forest with conservative hyperparameters."""
    features = MVP_FEATURES
    seasons = sorted(df["season"].unique())
    results = []
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test = df[df["season"] == holdout_season]

        X_train = train[features].values
        y_train = train["team_a_won"].values

        # Build inner LOYO folds for calibration
        train_seasons = sorted(train["season"].unique())
        cv_folds = []
        for inner_holdout in train_seasons:
            train_idx = np.where(train["season"].values != inner_holdout)[0]
            val_idx = np.where(train["season"].values == inner_holdout)[0]
            cv_folds.append((train_idx, val_idx))

        rf = RandomForestClassifier(
            n_estimators=100,
            max_depth=3,
            min_samples_leaf=20,
            random_state=42,
        )
        cal_rf = CalibratedClassifierCV(rf, method="isotonic", cv=cv_folds)
        cal_rf.fit(X_train, y_train)

        probs = cal_rf.predict_proba(test[features].values)[:, 1]
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Experiment F: Gradient Boosting ──────────────────────────────────────────

def run_exp_f(df: pd.DataFrame) -> pd.DataFrame:
    """Gradient Boosting with conservative hyperparameters."""
    features = MVP_FEATURES
    seasons = sorted(df["season"].unique())
    results = []
    for holdout_season in seasons:
        train = df[df["season"] != holdout_season]
        test = df[df["season"] == holdout_season]

        X_train = train[features].values
        y_train = train["team_a_won"].values

        gb = GradientBoostingClassifier(
            n_estimators=50,
            max_depth=2,
            learning_rate=0.1,
            min_samples_leaf=20,
            subsample=0.8,
            random_state=42,
        )
        gb.fit(X_train, y_train)
        probs = gb.predict_proba(test[features].values)[:, 1]
        out = base_result_columns(test)
        out["predicted_prob"] = probs
        results.append(out)
    return pd.concat(results, ignore_index=True)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    df = load_data()
    print(f"Loaded {len(df)} rows, seasons {df['season'].min()}-{df['season'].max()}\n")

    experiments = {
        "MVP (LR: em+seed+luck)": run_mvp,
        "A: KenPom-only (no seed)": run_exp_a,
        "B: Seed-residualized EM": run_exp_b,
        "C: Tuned regularization": run_exp_c,
        "D: Round interaction": run_exp_d,
        "E: Random Forest (cal.)": run_exp_e,
        "F: Gradient Boosting": run_exp_f,
    }

    all_metrics = []

    for name, run_fn in experiments.items():
        print(f"Running {name}...")
        preds = run_fn(df)
        y_true = preds["team_a_won"].values
        y_prob = preds["predicted_prob"].values

        metrics = compute_metrics(y_true, y_prob)
        metrics["experiment"] = name
        all_metrics.append(metrics)

        # Save predictions
        safe_name = name.split(":")[0].strip().lower().replace(" ", "_").replace("(", "").replace(")", "").replace(".", "")
        preds.to_csv(ARTIFACTS_DIR / f"preds_{safe_name}.csv", index=False)

    # Seed-only baseline
    print("Running seed-only baseline...")
    y_seed = seed_baseline_loyo(df)
    seed_metrics = compute_metrics(df["team_a_won"].values, y_seed)
    seed_metrics["experiment"] = "Seed-only baseline"
    all_metrics.append(seed_metrics)

    # Build comparison table
    results_df = pd.DataFrame(all_metrics)
    results_df = results_df[["experiment", "brier", "reliability", "log_loss", "auc", "accuracy"]]
    results_df = results_df.sort_values("brier")

    print("\n" + "=" * 90)
    print("EXPERIMENT COMPARISON (sorted by Brier score, lower is better)")
    print("=" * 90)
    print(results_df.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    print("=" * 90)

    # Save
    results_df.to_csv(ARTIFACTS_DIR / "experiment_comparison.csv", index=False)
    print(f"\nSaved experiment_comparison.csv and prediction CSVs to {ARTIFACTS_DIR}")


if __name__ == "__main__":
    main()
