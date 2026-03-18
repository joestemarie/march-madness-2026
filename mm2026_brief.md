# March Madness Prediction Model

## Project Overview

A phased system for predicting NCAA March Madness tournament outcomes (Rounds 1-2) with well-calibrated probabilities for betting on Kalshi. The system ingests college basketball data from the KenPom API and Kaggle, transforms it into modeling features via dbt + DuckDB, fits a logistic regression model with rigorous calibration validation, and outputs a bet sheet comparing model probabilities to Kalshi market prices.

**Design philosophy:** Local-first, reproducible, minimal dependencies. Build each phase fully before moving to the next. Validate assumptions at every layer.

## Tech Stack

- **Database:** DuckDB (file-based, `data/madness.duckdb`)
- **Ingestion:** Python scripts (one per source)
- **Transformation:** dbt-duckdb
- **Modeling:** Python (scikit-learn, pandas, matplotlib)
- **Language version:** Python 3.11+
- **Package management:** uv

## Project Structure

```
march-madness/
├── CLAUDE.md
├── pyproject.toml
├── .env                         # KENPOM_API_KEY (gitignored)
├── .env.example                 # Template showing required env vars
├── data/
│   └── madness.duckdb           # DuckDB database (gitignored)
├── seeds/                       # Manual CSV reference files
│   └── team_crosswalk.csv       # Maps team names across sources
├── ingestion/
│   ├── __init__.py
│   ├── ingest_kenpom.py         # KenPom API — team ratings by season
│   ├── ingest_barttorvik.py     # Barttorvik T-Rank ratings + game predictions
│   ├── ingest_kaggle.py         # Kaggle MMLM tournament results + seeds
│   └── utils.py                 # Shared DuckDB connection helpers
├── dbt_project/
│   ├── dbt_project.yml
│   ├── profiles.yml
│   ├── seeds/
│   │   └── team_crosswalk.csv   # Symlink or copy from /seeds
│   ├── models/
│   │   ├── staging/
│   │   │   ├── stg_kenpom_ratings.sql
│   │   │   ├── stg_kenpom_four_factors.sql
│   │   │   ├── stg_barttorvik_ratings.sql
│   │   │   ├── stg_barttorvik_game_predictions.sql
│   │   │   ├── stg_kaggle_tourney_results.sql
│   │   │   └── stg_kaggle_seeds.sql
│   │   └── marts/
│   │       ├── tournament_matchups.sql
│   │       └── model_features.sql
│   └── tests/
│       └── ...
├── modeling/
│   ├── __init__.py
│   ├── train.py                 # Model training with LOYO CV
│   ├── calibration.py           # Calibration plots + Brier decomposition
│   ├── evaluate.py              # Summary evaluation metrics
│   └── artifacts/               # Serialized models + reports (gitignored)
├── forecasting/
│   ├── __init__.py
│   ├── predict.py               # Generate predictions for current tournament
│   ├── edge.py                  # Compare model probs to Kalshi prices
│   └── allocate.py              # Kelly criterion bet sizing
└── notebooks/                   # Optional EDA / scratch work
    └── ...
```

---

## Phase 1: Data Ingestion + Transformation

**Goal:** Get clean, joined, tested data into DuckDB so that Phase 2 can query a single `model_features` table and start modeling immediately.

### 1A: Ingestion Scripts

Each ingestion script is idempotent — running it twice produces the same result. All scripts write to a `raw` schema in DuckDB.

#### `ingest_kenpom.py`

- **Source:** KenPom official API (https://kenpom.com/api/v1/)
- **Auth:** API key stored in `.env` as `KENPOM_API_KEY`, passed via `Authorization: Bearer <key>` header
- **Seasons:** 2002 through current season (KenPom data goes back to 2002; 1999-2001 exists but is less reliable)
- **Target tables:**
  - `raw.kenpom_ratings` — main team ratings table
  - `raw.kenpom_four_factors` — four factors breakdown (offense + defense)
- **API endpoints to call:**
  - `GET /ratings?season={year}` — AdjEM, AdjO, AdjD, AdjT, SOS, Luck, ranking
  - `GET /fourfactors?season={year}` — eFG%, TO%, OR%, FTRate on both ends
- **Columns to capture from /ratings:**
  - team_name, conference
  - adj_em (adjusted efficiency margin)
  - adj_o (adjusted offensive efficiency)
  - adj_d (adjusted defensive efficiency)
  - adj_t (adjusted tempo)
  - sos_adj_em (strength of schedule — AdjEM of opponents)
  - sos_adj_o, sos_adj_d (SOS offense/defense components)
  - luck (gap between actual and expected record)
  - ncsos_adj_em (non-conference SOS)
  - kenpom_rank
  - season (integer year)
- **Columns to capture from /fourfactors:**
  - team_name, conference
  - off_efg_pct (offensive effective FG%)
  - off_to_pct (offensive turnover rate)
  - off_or_pct (offensive rebounding rate)
  - off_ft_rate (offensive free throw rate)
  - def_efg_pct, def_to_pct, def_or_pct, def_ft_rate (defensive equivalents)
  - season (integer year)
- **Rate limiting:** Respect KenPom API rate limits. Add 0.5s delay between requests. Log each request.
- **Error handling:** If a season returns 404 or empty, log a warning and continue. Do not fail the entire run.
- **Notes:** Store all columns returned by the API, not just the ones listed above. We filter in dbt, not at ingestion. Check the API docs after purchase for exact endpoint paths and response shapes — the paths above are based on the registration page description and may need adjustment.

#### `ingest_kaggle.py`

- **Source:** Kaggle March Machine Learning Mania dataset
- **Files needed:**
  - `MNCAATourneyCompactResults.csv` — game results (season, winning team, losing team, scores)
  - `MNCAATourneySeeds.csv` — team seeds per season
  - `MTeams.csv` — team ID to name mapping
- **Target tables:**
  - `raw.kaggle_tourney_results`
  - `raw.kaggle_seeds`
  - `raw.kaggle_teams`
- **Method:** Read CSVs, load directly into DuckDB. These are static files the user downloads from Kaggle.
- **Notes:** The Kaggle dataset uses integer TeamIDs. The team name mapping is in MTeams.csv. Seed format is like "W01" (region + seed number) — preserve the raw format, parse in dbt.

#### `ingest_barttorvik.py`

- **Source:** Barttorvik T-Rank data via CBBData API (cbbdata.aweatherman.com) or direct CSV export from barttorvik.com
- **Auth:** Free CBBData API key (register via the `cbbdata` R package or hit the API directly)
- **Seasons:** 2008 through current season
- **Target tables:**
  - `raw.barttorvik_ratings` — team-level T-Rank ratings and adjusted metrics
  - `raw.barttorvik_game_predictions` — matchup-level game predictions (win %, projected score, tempo)
- **Data to capture for ratings (`raw.barttorvik_ratings`):**
  - team_name, conference
  - barthag (projected win % vs average D1 team — the core T-Rank rating)
  - adj_oe (adjusted offensive efficiency)
  - adj_de (adjusted defensive efficiency)
  - adj_tempo
  - t_rank (overall T-Rank ranking)
  - sos (strength of schedule components)
  - All four factors: off_efg, off_to, off_or, off_ftr, def_efg, def_to, def_or, def_ftr
  - season (integer year)
- **Data to capture for game predictions (`raw.barttorvik_game_predictions`):**
  - For each historical tournament matchup (Rounds 1-2): call the game prediction endpoint with both teams, the game date, and location='N' (neutral)
  - Columns: season, team_name, opponent_name, predicted_tempo, predicted_ppp (points per possession), predicted_pts (projected score), predicted_win_pct
  - This requires knowing the tournament matchups first — run AFTER `ingest_kaggle.py` so we can read matchups from `raw.kaggle_tourney_results`
  - For current-year predictions: run after bracket is announced
- **Access method (preferred — Python, no R required):**
  - CBBData has an underlying REST API. The R package is a wrapper, but the endpoints can be called directly with `httpx`
  - Team ratings: the CBBData Torvik endpoints (equivalent of `cbd_torvik_team_factors`)
  - Game predictions: the CBBData game prediction endpoint (equivalent of `cbd_torvik_game_prediction`)
  - If direct API access is problematic, fall back to: (a) scraping Barttorvik CSV exports for ratings, (b) using the `toRvik` R package via subprocess for game predictions
- **Rate limiting:** Be polite — 1 req/sec for any scraping, respect CBBData API limits
- **Key difference from KenPom:** Barttorvik applies recency bias (games older than 40 days are downweighted, reaching 60% weight at 80+ days). This means Barttorvik ratings capture late-season form better than KenPom's equal-weighted approach. The game predictions also use a log5 formula calibrated with an 11.5 exponent for the pythagorean expectation.
- **Notes:** This is ingested for future use — it is NOT used in the Phase 2 MVP model. It provides: (1) an alternative efficiency metric with recency weighting, (2) a pre-built game prediction that can serve as a feature or baseline, (3) a second data source for cross-validation of KenPom metrics. Store everything available. Do not filter columns at this stage.

### 1B: Team Name Crosswalk

**This is the most critical manual artifact in the project.**

KenPom, Barttorvik, and Kaggle use different team naming conventions. Examples: "UConn" (KenPom) vs "Connecticut" (Kaggle), "St. John's" vs "St John's", "USC" vs "Southern California".

Create `seeds/team_crosswalk.csv` with columns:
- `kaggle_team_id` (integer)
- `kaggle_team_name` (from MTeams.csv)
- `kenpom_team_name` (as it appears in KenPom API responses)
- `barttorvik_team_name` (as it appears in Barttorvik/CBBData responses)
- `canonical_name` (our standard name used in marts)

Population strategy:
1. Pull distinct team names from both sources
2. Start with exact matches (there will be many)
3. Use fuzzy matching (e.g. `rapidfuzz`) to identify close matches
4. Manually resolve the remaining ~20-40 mismatches
5. Only need to map teams that have appeared in the tournament (not all 350+ D1 teams)
6. Add a dbt test that asserts every tournament team has a crosswalk entry

### 1C: dbt Transformation Layer

Use the `dbt-duckdb` adapter. All models read from `raw` schema and write to `staging` or `marts` schemas.

#### Staging Models

**`stg_kenpom_ratings`**
- Source: `raw.kenpom_ratings`
- Clean column names (snake_case, consistent naming)
- Cast types explicitly (everything from API may come as strings)
- Add `source_name` column = 'kenpom'
- Join to crosswalk on `kenpom_team_name` to get `canonical_name` and `kaggle_team_id`

**`stg_kenpom_four_factors`**
- Source: `raw.kenpom_four_factors`
- Clean column names, cast types
- Join to crosswalk on `kenpom_team_name`
- This stays as a separate staging model from ratings — they get joined together in the mart layer

**`stg_barttorvik_ratings`**
- Source: `raw.barttorvik_ratings`
- Clean column names (snake_case, consistent naming)
- Cast types explicitly
- Join to crosswalk on `barttorvik_team_name` to get `canonical_name` and `kaggle_team_id`
- Not used in MVP model features, but available for future feature engineering

**`stg_barttorvik_game_predictions`**
- Source: `raw.barttorvik_game_predictions`
- Clean column names, cast types
- Join to crosswalk on both `team_name` and `opponent_name` to get canonical names
- One row per team per game (two rows per matchup) — keep this shape, the mart can pivot if needed
- Not used in MVP model features, but available as a future feature (Barttorvik predicted win %) or baseline comparison

**`stg_kaggle_tourney_results`**
- Source: `raw.kaggle_tourney_results`
- Parse into one row per game with: `season`, `team_id_winner`, `team_id_loser`, `score_winner`, `score_loser`
- Add `round` column derived from DayNum:
  - Round 1 (Round of 64): DayNum 134-135
  - Round 2 (Round of 32): DayNum 136-137
  - (Later rounds for future use)
- Join to crosswalk for canonical names

**`stg_kaggle_seeds`**
- Source: `raw.kaggle_seeds`
- Parse seed string: extract region (W/X/Y/Z) and seed number (1-16)
- Handle play-in teams (seeds like "W16a", "W16b") — assign seed 16
- Join to crosswalk for canonical names

#### Mart Models

**`tournament_matchups`**
- One row per tournament game, with columns for both teams
- Columns: `season`, `round`, `team_a_id`, `team_b_id`, `team_a_seed`, `team_b_seed`, `team_a_canonical_name`, `team_b_canonical_name`, `winner_id`
- Convention: `team_a` is always the higher-seeded (lower seed number) team. In case of same seed, alphabetical.
- This normalization ensures consistent feature differentials (always "better seed minus worse seed")

**`model_features`**
- One row per tournament matchup
- Join `tournament_matchups` to both `stg_kenpom_ratings` and `stg_kenpom_four_factors` for both teams
- Compute differentials (team_a minus team_b for all metrics):
  - **Core efficiency:**
    - `adj_em_diff` (adjusted efficiency margin difference — the key feature)
    - `adj_o_diff`, `adj_d_diff` (offensive/defensive efficiency separately)
    - `adj_t_diff` (tempo)
  - **Strength of schedule:**
    - `sos_adj_em_diff`
    - `ncsos_adj_em_diff` (non-conference SOS — captures schedule difficulty outside conference play)
  - **Luck:**
    - `luck_diff`
  - **Seed:**
    - `seed_diff`
  - **Four factors (for extended model):**
    - `off_efg_pct_diff`, `def_efg_pct_diff`
    - `off_to_pct_diff`, `def_to_pct_diff`
    - `off_or_pct_diff`, `def_or_pct_diff`
    - `off_ft_rate_diff`, `def_ft_rate_diff`
- Include raw values for both teams as well (for future feature engineering)
- Target variable: `team_a_won` (1 if higher-seeded team won, 0 otherwise)
- Filter to Rounds 1 and 2 only (for now)

#### dbt Tests

- `unique` and `not_null` on all primary keys
- Every team in `stg_kaggle_seeds` has a match in the crosswalk
- Every team in tournament matchups has KenPom ratings for that season
- `team_a_seed <= team_b_seed` in `tournament_matchups` (our convention)
- `model_features` has no null differentials
- Row count sanity: ~32 Round 1 games and ~16 Round 2 games per season
- KenPom ratings exist for all seasons in the tournament results table

### Phase 1 Definition of Done

- [ ] `.env` contains valid `KENPOM_API_KEY`
- [ ] `uv run python ingestion/ingest_kenpom.py` populates `raw.kenpom_ratings` and `raw.kenpom_four_factors` for 2002-current
- [ ] `uv run python ingestion/ingest_barttorvik.py` populates `raw.barttorvik_ratings` for 2008-current and `raw.barttorvik_game_predictions` for historical tournament matchups
- [ ] `uv run python ingestion/ingest_kaggle.py` populates `raw.kaggle_*` tables
- [ ] `team_crosswalk.csv` resolves all tournament teams across both sources
- [ ] `dbt build` passes with all tests green
- [ ] `select count(*) from marts.model_features where round = 1` returns ~32 * 23 = ~736 rows (2002-2024, minus 2020 COVID cancellation)
- [ ] `select count(*) from marts.model_features where round = 2` returns ~16 * 23 = ~368 rows
- [ ] Quick sanity check: query average `team_a_won` by seed matchup, confirm 1-seeds beat 16-seeds ~98% of the time

---

## Phase 2: Modeling + Validation

**Goal:** Fit a well-calibrated logistic regression model and rigorously validate that the predicted probabilities are trustworthy. Do NOT proceed to Phase 3 until calibration is verified.

### 2A: Model Training (`train.py`)

**Input:** Query `marts.model_features` from DuckDB.

**MVP feature set (start here):**
- `adj_em_diff`
- `seed_diff`
- `luck_diff`

**Extended feature set (try after MVP baseline):**
- Add `adj_o_diff`, `adj_d_diff` separately (instead of combined AdjEM)
- Add `adj_t_diff`
- Add `sos_adj_em_diff`
- Add `off_efg_pct_diff`, `def_efg_pct_diff` (four factors — effective FG% is the most impactful)

**Model:** `sklearn.linear_model.LogisticRegressionCV`
- Penalty: L2 (ridge)
- Cv: Use custom leave-one-year-out (LOYO) cross-validation folds — NOT random splits
- Solver: 'lbfgs'
- Standardize features (use `StandardScaler` in a `Pipeline`)

**LOYO cross-validation:**
- For each season Y in the dataset:
  - Train on all seasons except Y
  - Predict probabilities for season Y
  - Store predictions with actuals
- This produces out-of-sample predictions for every game in the dataset
- CRITICAL: Never allow data from the prediction year to leak into training

**Output:**
- Save LOYO predictions to `modeling/artifacts/loyo_predictions.csv` (season, team_a, team_b, predicted_prob, actual_outcome)
- Save final model (trained on all data) to `modeling/artifacts/model.pkl`
- Save feature coefficients + intercept for interpretability

### 2B: Calibration Validation (`calibration.py`)

This is the most important part of the entire project. A model with good discrimination but poor calibration is useless for betting.

**Calibration plot:**
- Bin LOYO predicted probabilities into deciles (or quintiles if sample is small)
- Plot predicted probability (bin midpoint) vs observed win rate
- Plot the ideal diagonal for reference
- Save to `modeling/artifacts/calibration_plot.png`

**Brier score decomposition:**
- Compute overall Brier score
- Decompose into reliability (calibration), resolution (discrimination), and uncertainty
- Reliability should be < 0.01 for a well-calibrated model
- Report all three components

**Additional metrics:**
- Log-loss (proper scoring rule, like Brier but more sensitive to confident wrong predictions)
- AUC-ROC (discrimination — useful but not sufficient for betting)
- Accuracy by round (Round 1 vs Round 2 separately)
- Accuracy by seed differential bucket (1v16, 2v15, ... 8v9)

**Calibration adjustment (if needed):**
- If calibration plot shows systematic bias, apply Platt scaling or isotonic regression
- Fit on inner CV loop (do not calibrate on the same data you evaluate on)
- Re-run calibration plot to confirm improvement

### 2C: Baseline Comparisons (`evaluate.py`)

Compare the model against naive baselines to confirm it adds value:

1. **Seed-only baseline:** Always predict the higher seed wins. Win probability = historical win rate for that seed matchup (e.g., 1v16 → 0.98, 8v9 → 0.52)
2. **KenPom ranking baseline:** Always predict the higher-KenPom-ranked team wins, probability based on historical win rate by ranking gap bucket
3. **Market baseline (if available):** Historical closing lines or implied probabilities

Report Brier score and log-loss for each baseline alongside the model.

### Phase 2 Definition of Done

- [ ] LOYO predictions generated for all seasons in the dataset
- [ ] Calibration plot shows predicted probabilities track observed rates (no systematic over/under-confidence)
- [ ] Brier score reliability component < 0.02
- [ ] Model outperforms seed-only baseline on Brier score
- [ ] Feature coefficients are interpretable and directionally correct (positive AdjEM diff → higher win prob)
- [ ] All artifacts saved to `modeling/artifacts/`
- [ ] Decision documented: are we using the MVP or extended feature set going forward?

---

## Phase 3: Forecasting + Allocating

**Goal:** Generate predictions for the current tournament, compare to Kalshi market prices, identify edges, and output a sized bet sheet.

### 3A: Current Tournament Predictions (`predict.py`)

- Re-run `ingest_kenpom.py` for the current season to get latest ratings (KenPom updates daily during the season)
- Re-run `dbt build` to refresh features
- Once bracket is announced, create a matchup file (manual or scraped) with Round 1 and Round 2 pairings
  - Round 2 pairings require predicting Round 1 winners or handling both possible matchups
- Load the trained model from `modeling/artifacts/model.pkl`
- Generate P(higher_seed_wins) for each matchup
- Output: `forecasting/artifacts/predictions_{year}.csv` with columns: `round`, `team_a`, `team_b`, `seed_a`, `seed_b`, `model_prob_a_wins`

**Round 2 handling:**
- Option A (simpler): Wait until Round 1 is complete, then predict Round 2 with known matchups
- Option B (full bracket): Generate predictions for all possible Round 2 matchups (32 combinations), then filter to actuals after Round 1
- Start with Option A for the MVP

### 3B: Edge Detection (`edge.py`)

- **Input:** Model predictions + Kalshi contract prices
- Kalshi prices are manually entered or scraped (manual for MVP — just a CSV with contract name, team, implied probability)
- Compute edge: `model_prob - kalshi_implied_prob`
- Flag bets where `abs(edge) > threshold`
  - Default threshold: 5 percentage points
  - This is configurable and should be explored

**Output:** `forecasting/artifacts/edges_{year}.csv` with: `matchup`, `model_prob`, `kalshi_price`, `edge`, `edge_pct`, `bet_side` (which team to bet), `confidence_tier` (high/medium/low based on edge size)

### 3C: Bet Allocation (`allocate.py`)

- **Input:** Edges + bankroll ($250 default)
- **Sizing method:** Fractional Kelly criterion
  - Full Kelly: `f* = (bp - q) / b` where b = odds, p = model prob, q = 1-p
  - Use **quarter Kelly** (f*/4) for conservatism — we have model uncertainty and small sample
  - Minimum bet size: $5 (below this, skip — not worth the overhead)
  - Maximum single bet: 15% of bankroll ($37.50) regardless of Kelly output
- **Risk controls:**
  - Total allocated cannot exceed bankroll
  - If Kelly sizing exceeds bankroll, scale all bets proportionally
  - Report expected value, expected profit, and probability of losing entire bankroll (simple simulation)

**Output:** `forecasting/artifacts/bet_sheet_{year}.csv` with: `matchup`, `bet_side`, `kalshi_price`, `model_prob`, `edge`, `kelly_fraction`, `bet_amount`, `expected_profit`

Also output a summary:
- Total bets placed
- Total capital deployed
- Expected profit (sum of individual EVs)
- Breakeven analysis (how many bets need to win to cover the ~$20 KenPom subscription cost)

### Phase 3 Definition of Done

- [ ] Predictions generated for all Round 1 matchups
- [ ] Edge detection identifies specific bets with > 5pt edge
- [ ] Bet sheet allocates $250 bankroll with quarter-Kelly sizing
- [ ] All bets respect min/max constraints
- [ ] Summary report includes expected profit and breakeven analysis
- [ ] After tournament: actual results logged, realized profit computed, model updated with new season data

---

## Implementation Notes

### DuckDB Connection Pattern
```python
import duckdb

def get_connection(db_path: str = "data/madness.duckdb") -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(db_path)
    conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
    return conn
```

### KenPom API Pattern
```python
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

KENPOM_API_KEY = os.environ["KENPOM_API_KEY"]
BASE_URL = "https://kenpom.com/api/v1"

def kenpom_get(endpoint: str, params: dict = None) -> dict:
    """Make an authenticated GET request to the KenPom API."""
    headers = {"Authorization": f"Bearer {KENPOM_API_KEY}"}
    resp = httpx.get(f"{BASE_URL}/{endpoint}", headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()

# Example: get ratings for 2024 season
ratings = kenpom_get("ratings", {"season": 2024})
```

**Note:** The exact API base URL, endpoint paths, and response shapes should be confirmed against the API documentation provided with your API key. The patterns above are based on the KenPom registration page description. Adjust as needed after reviewing the actual docs.

### Data Source Notes

- **KenPom API:** ~$20/year subscription. Official REST API with endpoints for ratings, four factors, SOS, tempo. API key via kenpom.com/register-api.php. Data available from 2002 (1999-2001 less reliable). KenPom is the gold standard for adjusted efficiency metrics in college basketball. Primary metrics source for the model.
- **Barttorvik / CBBData:** Free. Team ratings from 2008-present, game predictions for any historical or hypothetical matchup. Access via CBBData API (cbbdata.aweatherman.com) or direct CSV export from barttorvik.com. Key differentiator from KenPom: recency-weighted ratings and a log5-based game predictor. Ingested for future use — not in MVP model.
- **Kaggle MMLM:** Free. Download CSVs manually from kaggle.com/competitions/march-machine-learning-mania. Updated annually. Static historical data.

### Key Assumptions

- We always orient matchups so team_a is the higher seed (lower number). This means `model_prob` is always P(higher_seed_wins).
- Seasons use the spring year convention (2024 = the 2023-24 season).
- We focus on Rounds 1 and 2 only. The architecture supports extending to later rounds.
- The model is intentionally simple (logistic regression). Complexity comes from good features and rigorous validation, not model architecture.
- KenPom data is the primary metrics source for the MVP model. Barttorvik data is ingested and staged but reserved for future use — potential applications include: using Barttorvik's predicted win % as a model feature, using recency-weighted ratings as alternative features, or using Barttorvik predictions as a calibration baseline.

### What NOT To Do

- Do not use random forest, XGBoost, or neural nets. ~1100 training observations (with KenPom's deeper history) is still not enough to justify complex models.
- Do not do random train/test splits across years. Always use LOYO CV.
- Do not add features that aren't available pre-tournament (e.g., Round 1 box scores for Round 2 predictions in Option A).
- Do not bet without confirming calibration. A discriminative but miscalibrated model will lose money.
- Do not hard-code the KenPom API key anywhere. Always read from `.env`.
- Do not hammer the KenPom API — add delays between requests and cache responses in DuckDB.
