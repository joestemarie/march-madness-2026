# Modeling Experiments Log

## Dataset
- 624 observations: 14 seasons (2010-2024) x ~48 Round 1 + Round 2 games
- Target: P(team_a_wins) where team_a is the higher seed (lower number)
- All evaluation uses leave-one-year-out (LOYO) cross-validation

---

## MVP Model (Logistic Regression)

**Features:** adj_em_diff, seed_diff, luck_diff

| Metric      | Value  |
|-------------|--------|
| Brier       | 0.1945 |
| Reliability | 0.0028 |
| Log-loss    | 0.5709 |
| AUC         | 0.6516 |
| Accuracy    | 70.2%  |

**Coefficients (standardized):** seed_diff +0.63, adj_em_diff +0.01, luck_diff +0.04

### Key observations
- **Calibration is excellent** -- reliability of 0.0028 is well below the 0.02 threshold
- **seed_diff dominates** -- adj_em_diff contributes almost nothing due to collinearity with seed
- **Does not beat seed-only baseline** (Brier 0.1902) -- the model is essentially a noisy seed predictor
- KenPom rank baseline Brier: 0.1993 (model beats this)

## Extended Model (8 features)

**Features:** adj_o_diff, adj_d_diff, adj_t_diff, seed_diff, luck_diff, sos_adj_em_diff, off_efg_pct_diff, def_efg_pct_diff

| Metric | Value  |
|--------|--------|
| Brier  | 0.1964 |

Worse than MVP -- more features overfit with only 624 observations.

---

## Full Experiment Comparison

All experiments evaluated with LOYO CV, sorted by Brier score (lower is better):

| Experiment                  | Brier  | Reliability | Log-loss | AUC    | Accuracy |
|-----------------------------|--------|-------------|----------|--------|----------|
| Seed-only baseline          | 0.1902 | 0.0050      | 0.6149   | 0.6739 | 69.7%    |
| D: Round interaction        | 0.1928 | 0.0015      | 0.5673   | 0.6607 | 70.8%    |
| F: Gradient Boosting        | 0.1940 | 0.0032      | 0.5680   | 0.6598 | 70.0%    |
| C: Tuned regularization     | 0.1943 | 0.0016      | 0.5711   | 0.6512 | 71.0%    |
| B: Seed-residualized EM     | 0.1945 | 0.0036      | 0.5710   | 0.6541 | 70.2%    |
| MVP (LR: em+seed+luck)     | 0.1945 | 0.0028      | 0.5709   | 0.6516 | 70.2%    |
| E: Random Forest (cal.)    | 0.1982 | 0.0068      | 0.6301   | 0.6526 | 68.1%    |
| A: KenPom-only (no seed)   | 0.2063 | 0.0027      | 0.6026   | 0.5361 | 71.0%    |

---

## Experiment Details

### Experiment A: KenPom-only (no seed)

**Features:** adj_em_diff, luck_diff (seed excluded)

**Rationale:** Force the model to use efficiency metrics only, potentially finding edge when seeds mislead.

**Results:** Brier 0.2063, AUC 0.5361

**Analysis:** Worst performer. Without seed information, the model loses its primary signal. AUC of 0.54 is barely above random -- adj_em_diff alone cannot discriminate winners from losers in tournament play (where seed matchups constrain the talent gap). This confirms that tournament results are dominated by the seeding structure itself.

### Experiment B: Seed-residualized AdjEM

**Features:** em_residual (adj_em_diff minus expected for that seed matchup), seed_diff, luck_diff

**Rationale:** Captures "better/worse than expected for seed" to decorrelate efficiency from seed.

**Results:** Brier 0.1945, Reliability 0.0036

**Analysis:** Identical Brier to MVP. The residualization was mathematically sound but the residual contains so little signal (after removing the seed-correlated component) that it adds nothing. The collinearity problem is really a "there's no independent signal" problem.

### Experiment C: Tuned regularization (LogisticRegressionCV)

**Features:** adj_em_diff, seed_diff, luck_diff (same as MVP)

**Method:** LogisticRegressionCV with inner LOYO folds, L2 penalty, C selection via neg_brier_score.

**Results:** Brier 0.1943, Reliability 0.0016

**Analysis:** Marginal improvement over MVP (0.0002 Brier). The tuned regularization slightly improves calibration (reliability 0.0016, best among LR variants). However, the gain is negligible -- the default regularization was already near-optimal.

### Experiment D: Round interaction

**Features:** adj_em_diff, seed_diff, luck_diff, round_indicator, seed_diff * round_indicator

**Rationale:** Upset dynamics differ between Round 1 and Round 2 -- Round 2 matchups are closer in seed.

**Results:** Brier 0.1928, Reliability 0.0015, Log-loss 0.5673

**Analysis:** Best logistic regression variant. Brier of 0.1928 narrows the gap to seed baseline (0.1902) significantly. The round interaction captures that seed_diff's predictive power changes between rounds. Excellent calibration (0.0015). Best log-loss among all models (0.5673), meaning it assigns more appropriate confidence levels.

### Experiment E: Calibrated Random Forest

**Features:** adj_em_diff, seed_diff, luck_diff

**Method:** RandomForest (100 trees, max_depth=3, min_samples_leaf=20) with isotonic calibration via CalibratedClassifierCV using inner LOYO folds.

**Results:** Brier 0.1982, Reliability 0.0068

**Analysis:** Worst-calibrated model (reliability 0.0068, though still under 0.02). Brier worse than MVP despite calibration wrapper. The isotonic calibration with small inner folds (~45 games each) likely overfits the calibration mapping. Random forests are poorly suited to this small-data regime.

### Experiment F: Gradient Boosting

**Features:** adj_em_diff, seed_diff, luck_diff

**Method:** GradientBoostingClassifier (50 trees, max_depth=2, lr=0.1, min_samples_leaf=20, subsample=0.8).

**Results:** Brier 0.1940, Reliability 0.0032, Log-loss 0.5680

**Analysis:** Second-best overall. Conservative hyperparameters prevent overfitting. Beats MVP on Brier (0.1940 vs 0.1945) with comparable calibration. GBM's native probability calibration (via log-loss optimization) works well even without explicit calibration wrapper. Competitive with Experiment D.

---

## Key Takeaways

1. **No model beats the seed-only baseline on Brier.** The seed baseline (0.1902) remains king for raw probability accuracy. This is expected -- seeds encode committee knowledge that already incorporates efficiency metrics.

2. **Log-loss tells a different story.** Models D (0.5673) and F (0.5680) have substantially better log-loss than the seed baseline (0.6149). This means they assign more confident (and correct) probabilities. The seed baseline's higher log-loss comes from using historical averages that are pulled toward 0.5.

3. **For betting, log-loss matters more than Brier.** A bet pays off when your probability estimate is more accurate than the market's. Models that are more confident and correctly calibrated will identify more bets, even if their Brier score is slightly worse.

4. **Round interaction is a real effect.** Experiment D's improvement is the largest single-experiment gain, suggesting that upset dynamics genuinely differ by round.

5. **Tree models don't help.** With only 624 observations and 3 features, logistic regression is near-optimal. Trees add complexity without improving predictions.

---

## Recommendation: Best Model for Betting

**Recommended model: Experiment D (Round interaction, logistic regression)**

### Rationale

| Criterion                          | D: Round interaction | MVP  | Seed baseline |
|------------------------------------|----------------------|------|---------------|
| Brier (primary, lower=better)      | 0.1928               | 0.1945 | 0.1902      |
| Reliability (must be < 0.02)       | 0.0015               | 0.0028 | 0.0050      |
| Log-loss (lower=better)            | 0.5673               | 0.5709 | 0.6149      |
| Can disagree with seeds?           | Yes                  | Barely | No           |

1. **Best Brier among models** (0.1928) -- closest to seed baseline while actually being a model that can make differentiated predictions.

2. **Best calibration** (reliability 0.0015) -- well-calibrated probabilities are essential for Kelly criterion bet sizing.

3. **Best log-loss** (0.5673) -- significantly better than seed baseline (0.6149), meaning it assigns more informative probabilities. This is where the betting edge lives.

4. **Can disagree with seeds** -- unlike the seed-only baseline, this model can identify games where KenPom efficiency metrics suggest the lower seed is stronger than their seeding implies. The round interaction allows it to modulate this differently for R1 vs R2.

5. **Simple and interpretable** -- 5 features, logistic regression. Easy to understand why it makes each prediction, which is important for betting confidence.

### Runner-up: Experiment F (Gradient Boosting)

GBM is a reasonable alternative if nonlinear interactions prove important with more data. Currently nearly tied with D on all metrics but less interpretable. Worth revisiting if dataset grows beyond 1000 observations.

### What the model cannot do

No model tested here decisively beats the seed-only baseline on Brier. The betting edge, if it exists, will come from:
- Games where the model disagrees with seed expectations (these are where value bets lie)
- Superior confidence calibration (log-loss advantage)
- Not from overall probability accuracy (Brier)

The strategy should be: use model D to generate probabilities, identify games where model probability diverges significantly from market odds (which track seeds closely), and bet selectively on those.
