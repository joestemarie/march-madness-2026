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
├── .env                         # API keys (gitignored)
├── .env.example                 # Template showing required env vars
├── data/
│   └── madness.duckdb           # DuckDB database (gitignored)
├── dbt_project/seeds/
│   ├── team_crosswalk.csv       # Maps team names across sources
│   └── manual_seeds.csv         # Current year tournament seeds (kenpom names)
├── ingestion/
│   ├── __init__.py
│   ├── ingest_kenpom.py         # KenPom API — team ratings by season
│   ├── ingest_barttorvik.py     # Barttorvik T-Rank ratings + game predictions
│   ├── ingest_kaggle.py         # Kaggle MMLM tournament results + seeds
│   ├── ingest_kalshi.py         # Kalshi API — game winner market prices
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
│   ├── allocate.py              # Kelly criterion bet sizing
│   ├── execute.py               # Place orders on Kalshi (dry-run default)
│   ├── kalshi_client.py         # Authenticated Kalshi API client
│   └── artifacts/               # Predictions, edges, bet sheets (gitignored)
└── notebooks/                   # Optional EDA / scratch work
    └── ...
```

---

## Phase 1: Data Ingestion + Transformation

**Goal:** Get clean, joined, tested data into DuckDB so that Phase 2 can query a single `model_features` table and start modeling immediately.

### 1A: Ingestion Scripts

Each ingestion script is idempotent — running it twice produces the same result. All scripts write to a `raw` schema in DuckDB.

#### `ingest_kenpom.py`

- **Source:** KenPom official API (https://kenpom.com/api.php)
- **Auth:** API key stored in `.env` as `KENPOM_API_KEY`, passed via `Authorization: Bearer <key>` header
- **Seasons:** 2010 through current season (API returns 404 for 2002-2009; those seasons are not available via the current API)
- **Target tables:**
  - `raw.kenpom_ratings` — main team ratings table
  - `raw.kenpom_four_factors` — four factors breakdown (offense + defense)
- **API endpoints to call:**
  - `GET /api.php?endpoint=ratings&y={year}` — AdjEM, AdjO, AdjD, AdjT, SOS, Luck, ranking
  - `GET /api.php?endpoint=four-factors&y={year}` — eFG%, TO%, OR%, FTRate on both ends
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

- **Source:** Barttorvik T-Rank data via CBBData API (https://www.cbbdata.com)
- **Auth:** Free CBBData API key, passed as `key` query parameter (not Bearer token). Register via the `cbbdata` R package's `cbd_create_account()` function.
- **Seasons:** 2008 through current season
- **Target tables:**
  - `raw.barttorvik_ratings` — team-level T-Rank ratings and adjusted metrics
  - `raw.barttorvik_game_predictions` — matchup-level game predictions (win %, projected score, tempo)
- **Data to capture for ratings (`raw.barttorvik_ratings`):**
  - team, conf (conference)
  - barthag (projected win % vs average D1 team — the core T-Rank rating)
  - adj_o (adjusted offensive efficiency), adj_d (adjusted defensive efficiency)
  - adj_t (adjusted tempo)
  - barthag_rk, adj_o_rk, adj_d_rk, adj_t_rk (rankings)
  - wab (wins above bubble)
  - SOS columns: nc_elite_sos, nc_fut_sos, nc_cur_sos, ov_elite_sos, ov_fut_sos, ov_cur_sos
  - seed (if seeded)
  - season (integer year)
- **API endpoint for ratings:** `GET /api/torvik/ratings?key={key}&year={year}`
  - Returns **Parquet** (not JSON) — requires `pyarrow` dependency
- **Data to capture for game predictions (`raw.barttorvik_game_predictions`):**
  - For each historical R1/R2 tournament matchup (DayNum 134-137): call the game prediction endpoint with both teams, the game date (YYYYMMDD), and location='N' (neutral)
  - Columns: team, date (epoch ms), location, tempo, ppp (points per possession), pts (projected score), win_per (predicted win %), did_win, season, game_day_num
  - Returns **JSON** — 2 rows per matchup (one per team)
  - Requires kaggle data loaded first for matchups and MSeasons.csv for DayNum-to-date conversion
- **API endpoint for predictions:** `GET /api/torvik/game/prediction?key={key}&team={name}&opp={name}&date={YYYYMMDD}&location=N`
  - Predictions available from 2015 season onward
- **Rate limiting:** 0.5s delay between requests (2 req/sec)
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
4. Manually resolve the remaining mismatches via hardcoded overrides in `build_crosswalk.py` (~13 abbreviation-only names like FGCU, ULM, MTSU, WKU, ETSU)
5. Only need to map teams that have appeared in the tournament (~313 teams, not all 350+ D1 teams)
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
- Join to crosswalk on `team` column (Barttorvik team name) to get canonical name and kaggle_team_id
- One row per team per game (two rows per matchup) — the API returns both sides of each matchup
- Not used in MVP model features, but available as a future feature (Barttorvik predicted win %) or baseline comparison

**`stg_kaggle_tourney_results`**
- Source: `raw.kaggle_tourney_results`
- Parse into one row per game with: `season`, `team_id_winner`, `team_id_loser`, `score_winner`, `score_loser`
- Add `round` column derived from DayNum:
  - Round 0 (First Four / play-in): DayNum 134-135
  - Round 1 (Round of 64): DayNum 136-137
  - Round 2 (Round of 32): DayNum 138-139
  - Round 3 (Sweet 16): DayNum 143-144
  - Round 4 (Elite 8): DayNum 145-146
  - Round 5 (Final Four): DayNum 152
  - Round 6 (Championship): DayNum 154
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
- Row count sanity: ~32 Round 1 games and ~16 Round 2 games per season (lower bound 16 for COVID-affected 2021)
- `model_features` filtered to only rows with KenPom data (no null differentials)

### Phase 1 Definition of Done

- [x] `.env` contains valid `KENPOM_API_KEY`, `CBBDATA_API_KEY`, `KAGGLE_API_TOKEN`, `KALSHI_ACCESS_KEY`, `KALSHI_PRIVATE_KEY_PATH`
- [x] `make ingest-kenpom` populates `raw.kenpom_ratings` (6,014 rows, 2010-2026) and `raw.kenpom_four_factors` (8,679 rows, 2002-2026)
- [x] `make ingest-barttorvik` populates `raw.barttorvik_ratings` (6,324 rows, 2008-2025) and `raw.barttorvik_game_predictions` (270 rows, R1/R2 2015-2024)
- [x] `make ingest-kaggle` populates `raw.kaggle_tourney_results` (2,518), `raw.kaggle_seeds` (2,626), `raw.kaggle_teams` (380)
- [x] `team_crosswalk.csv` resolves all 313 tournament teams across all three sources (fuzzy matching + 13 manual overrides)
- [x] `dbt build` passes with all 33 tests green
- [x] `select count(*) from staging.model_features where round = 1` returns 412 rows (14 KenPom seasons: 2010-2024, minus 2020 COVID, ~32 games/season)
- [x] `select count(*) from staging.model_features where round = 2` returns 212 rows
- [x] Sanity check: 1-seeds beat 16-seeds 95.6% of the time, 8v9 matchups at 47.4% — directionally correct

---

## Phase 2: Modeling + Validation

**Goal:** Fit a well-calibrated logistic regression model and rigorously validate that the predicted probabilities are trustworthy. Do NOT proceed to Phase 3 until calibration is verified.

### 2A: Model Training (`train.py`)

**Input:** Query `staging.model_features` from DuckDB (~624 rows: 14 seasons × ~48 R1+R2 games, minus 2020 COVID).

**MVP feature set (start here):**
- `adj_em_diff`
- `seed_diff`
- `luck_diff`

**Extended feature set (try after MVP baseline):**
- Add `adj_o_diff`, `adj_d_diff` separately (instead of combined AdjEM)
- Add `adj_t_diff`
- Add `sos_adj_em_diff`
- Add `off_efg_pct_diff`, `def_efg_pct_diff` (four factors — effective FG% is the most impactful)

**Selected model (after experimentation): Round interaction LR**
- Features: `adj_em_diff`, `seed_diff`, `luck_diff`, `round_ind` (R2 indicator), `seed_diff * round_ind`
- The round interaction captures that seed predictive power differs between R1 and R2
- `sklearn.linear_model.LogisticRegression` with L2 penalty in a `Pipeline` with `StandardScaler`
- Cv: Custom leave-one-year-out (LOYO) cross-validation folds — NOT random splits
- Solver: 'lbfgs'

**LOYO cross-validation:**
- For each season Y in the dataset:
  - Train on all seasons except Y
  - Predict probabilities for season Y
  - Store predictions with actuals
- This produces out-of-sample predictions for every game in the dataset
- CRITICAL: Never allow data from the prediction year to leak into training

**Output:**
- Save LOYO predictions to `modeling/artifacts/loyo_predictions.csv` (season, team_a, team_b, predicted_prob, actual_outcome)
- Save final model (trained on all data) to `modeling/artifacts/model.pkl` (dict with pipeline, feature_set name, feature list)
- Save feature coefficients + intercept to `modeling/artifacts/coefficients.csv`
- Experiment comparison: `modeling/artifacts/experiment_comparison.csv`
- Full experiment log: `modeling/EXPERIMENTS.md`

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
- Reliability should be < 0.02 for a well-calibrated model
- Report all three components

**Additional metrics:**
- Log-loss (proper scoring rule, like Brier but more sensitive to confident wrong predictions)
- AUC-ROC (discrimination — useful but not sufficient for betting)
- Accuracy by round (Round 1 vs Round 2 separately)
- Accuracy by seed differential bucket (1v16, 2v15, ... 8v9)

**Calibration adjustment (not needed):**
- Calibration plot shows no systematic bias — reliability 0.0015 is excellent
- Platt scaling / isotonic regression were not applied; raw logistic regression probabilities are well-calibrated
- Isotonic calibration was tested via CalibratedClassifierCV on Random Forest (Experiment E) but hurt performance due to small inner folds

### 2C: Baseline Comparisons (`evaluate.py`)

Compare the model against naive baselines to confirm it adds value:

1. **Seed-only baseline:** Always predict the higher seed wins. Win probability = historical win rate for that seed matchup (e.g., 1v16 → 0.98, 8v9 → 0.52)
2. **KenPom ranking baseline:** Always predict the higher-KenPom-ranked team wins, probability based on historical win rate by ranking gap bucket
3. **Market baseline (if available):** Historical closing lines or implied probabilities

Report Brier score and log-loss for each baseline alongside the model.

### Phase 2 Definition of Done

- [x] LOYO predictions generated for all seasons in the dataset (624 rows, 14 seasons)
- [x] Calibration plot shows predicted probabilities track observed rates (no systematic over/under-confidence)
- [x] Brier score reliability component < 0.02 (achieved: 0.0015)
- [x] Model outperforms seed-only baseline on log-loss (0.567 vs 0.615) and accuracy (70.8% vs 69.7%). Does NOT beat seed baseline on Brier (0.193 vs 0.190) — see note below.
- [x] Feature coefficients are interpretable and directionally correct (positive seed_diff → higher win prob, positive adj_em_diff → higher win prob, round interaction negative → seed matters less in R2)
- [x] All artifacts saved to `modeling/artifacts/`
- [x] Decision documented: round interaction LR (Experiment D) selected over MVP, extended, tuned regularization, seed-residualized, Random Forest, and Gradient Boosting. See `modeling/EXPERIMENTS.md`.

**Note on Brier vs seed baseline:** No model tested beats the seed-only baseline on Brier score. This is expected — seeds encode selection committee knowledge that already incorporates efficiency metrics. However, the model has substantially better log-loss (0.567 vs 0.615), meaning it assigns more confident and correctly calibrated probabilities. For betting, this is what matters: the edge comes from games where model probability diverges from market odds (which track seeds closely), not from overall probability accuracy.

---

## Phase 3: Forecasting + Allocating

**Goal:** Generate predictions for the current tournament, compare to Kalshi market prices, identify edges, and output a sized bet sheet. Execute bets via the Kalshi API.

### 3A: Kalshi Market Data Ingestion (`ingest_kalshi.py`)

- **Source:** Kalshi public API — no authentication required for market data
- **Base URL:** `https://api.elections.kalshi.com/trade-api/v2`
- **Series:** `KXNCAAMBGAME` (individual game winner markets)
- **Target table:** `raw.kalshi_ncaambgame` (~10,474 rows covering full NCAAM season)
- **Key fields per market:** `ticker`, `event_ticker`, `title`, `subtitle` (team name), `yes_bid_dollars`, `yes_ask_dollars`, `implied_prob` (midpoint of bid/ask), `volume`, `status`
- **Pricing model:** Prices are fixed-point dollar strings (e.g., `"0.5300"` = 53 cents = 53% implied probability). Contracts pay $1.00 if YES.
- **Rate limits:** 20 req/sec (Basic tier). We fetch 1000 markets/request, so a full sweep takes ~10 paginated calls.
- **Notes:** The `KXNCAAMBGAME` series covers ALL college basketball games (regular season, NIT, tournament). Tournament games are filtered by matching to the seeds table. Other series available: `KXMARMADROUND` (advancement futures), `KXNCAAMBSPREAD` (spreads), `KXNCAAMBTOTAL` (totals).

### 3B: Tournament Seeds (`manual_seeds.csv`)

- **Location:** `dbt_project/seeds/manual_seeds.csv` — loaded via `dbt seed`
- **Columns:** `season`, `kenpom_team_name`, `seed_number`, `region`
- **Key design:** Uses `kenpom_team_name` as the join key (not `kaggle_team_id`) because many current tournament teams lack crosswalk entries. The seed is unioned into `stg_kaggle_seeds` alongside historical Kaggle seeds.
- **Maintenance:** For future years, add rows to this CSV with the new bracket and run `make dbt`.
- **Source:** Bracket data scraped from web after Selection Sunday announcement. 64 teams for 2026 (post-First Four).

### 3C: Current Tournament Predictions (`predict.py`)

- Reads active Kalshi game markets to discover matchups automatically
- Joins Kalshi team names → KenPom names via crosswalk + hardcoded overrides for Kalshi-specific names (e.g., "UConn" → "Connecticut", "Hawai'i" → "Hawaii", "Miami (FL)" → "Miami FL")
- Loads seeds from `staging.stg_kaggle_seeds` (database, via dbt)
- Filters to tournament-only games by requiring both teams to have seeds (NIT/CBI games excluded)
- Orients matchups so team_a = higher seed (lower number), matching training convention
- Loads trained model from `modeling/artifacts/model.pkl`
- Constructs features: `adj_em_diff`, `seed_diff`, `luck_diff`, `round_ind`, `seed_round_ix`
- Generates P(higher_seed_wins) for each matchup
- Output: `forecasting/artifacts/predictions_{year}.csv` with model probs and Kalshi implied probs side-by-side

**Round 2 handling:** Option A (wait for R1 results). Re-run `make forecast` after R1 completes — Kalshi will have R2 markets active, and the script auto-discovers them. Pass `--round 2` to set `round_ind = 1`.

**2026 R1 results:** 32 of 32 tournament games matched. Model systematically assigns higher upset probabilities than Kalshi (logistic regression is more moderate than market for heavy favorites).

### 3D: Edge Detection (`edge.py`)

- **Input:** Model predictions + Kalshi implied probabilities (from `predictions_{year}.csv`)
- Evaluates both sides of each game: team_a edge = `model_prob_a - kalshi_prob_a`, team_b edge = `(1 - model_prob_a) - kalshi_prob_b`
- Picks the side with the larger positive edge
- Confidence tiers: high (≥15pt), medium (≥10pt), low (≥5pt threshold)
- **Output:** `forecasting/artifacts/edges_{year}.csv`

**2026 R1 results:** 18 actionable edges found (> 5pt). All on underdogs — model assigns higher upset probabilities than Kalshi. Largest edges: McNeese +16.6% vs Vanderbilt, Penn +16.0% vs Illinois, TCU +14.2% vs Ohio St.

### 3E: Bet Allocation (`allocate.py`)

- **Input:** Edges + bankroll ($250 default)
- **Sizing:** Quarter-Kelly (`f*/4`), min $5, max 15% of bankroll
- **Risk controls:** Scale proportionally if total exceeds bankroll
- **Monte Carlo:** 10,000 simulation runs for profit distribution
- **Output:** `forecasting/artifacts/bet_sheet_{year}.csv`

**2026 R1 results:** 16 bets, $126.41 deployed of $250 bankroll. Monte Carlo: 82.4% P(profit > 0), median profit $231.68, 5th percentile -$76.19.

### 3F: Order Execution (`execute.py`)

- **Auth:** RSA-PSS signing with private key. Headers: `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, `KALSHI-ACCESS-SIGNATURE` (base64-encoded RSA-PSS of `{timestamp_ms}{METHOD}{/trade-api/v2/path}`)
- **Endpoint:** `POST /trade-api/v2/portfolio/orders` with `ticker`, `side` (yes/no), `action` (buy), `count`, `yes_price_dollars`
- **Credentials:** `KALSHI_ACCESS_KEY` and `KALSHI_PRIVATE_KEY_PATH` in `.env`. Private key file (`.pem`) is gitignored.
- **Dry run (default):** Resolves bet sheet → market tickers, displays full order summary without placing orders
- **Live mode:** `--live` flag. Checks account balance, warns if cost exceeds balance, prompts per-order confirmation (skip with `--confirm`)
- **Execution log:** Saves order IDs and fill status to `forecasting/artifacts/execution_log_{year}.csv`
- **Client:** `forecasting/kalshi_client.py` — reusable authenticated client with methods for balance, orders, positions

### Phase 3 Pipeline

```bash
make forecast     # ingest-kalshi → predict → edges → allocate
make execute      # dry run — preview orders
make execute-live # place real orders (with per-order confirmation)
```

### Phase 3 Definition of Done

- [x] Predictions generated for all Round 1 matchups (32/32 games matched)
- [x] Edge detection identifies specific bets with > 5pt edge (18 edges found, 16 above min bet)
- [x] Bet sheet allocates $250 bankroll with quarter-Kelly sizing ($126.41 deployed)
- [x] All bets respect min/max constraints ($5 min, $37.50 max)
- [x] Summary report includes expected profit and breakeven analysis (Monte Carlo: 82.4% P(profit > 0))
- [x] Kalshi API integration: live market data ingestion, authenticated order placement with dry-run mode
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
BASE_URL = "https://kenpom.com/api.php"

def kenpom_get(endpoint: str, params: dict = None) -> dict:
    """Make an authenticated GET request to the KenPom API."""
    headers = {"Authorization": f"Bearer {KENPOM_API_KEY}"}
    if params is None:
        params = {}
    params["endpoint"] = endpoint
    resp = httpx.get(BASE_URL, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()

# Example: get ratings for 2024 season
ratings = kenpom_get("ratings", {"y": 2024})
```

### Data Source Notes

- **KenPom API:** ~$20/year subscription. API at `kenpom.com/api.php` with `endpoint` query param (ratings, four-factors). API key via kenpom.com/register-api.php. Data available from 2010 (API returns 404 for earlier seasons). KenPom is the gold standard for adjusted efficiency metrics in college basketball. Primary metrics source for the model.
- **Barttorvik / CBBData:** Free. API at `www.cbbdata.com` (NOT `cbbdata.aweatherman.com` — the old URL is defunct). Auth via `key` query parameter. Ratings endpoint returns Parquet (requires `pyarrow`); game prediction endpoint returns JSON. Team ratings from 2008-present, game predictions for historical matchups from 2015-present. Key differentiator from KenPom: recency-weighted ratings and a log5-based game predictor. Ingested for future use — not in MVP model.
- **Kaggle MMLM:** Free. Download CSVs via `kaggle` CLI or manually from kaggle.com/competitions/march-machine-learning-mania. Updated annually. Static historical data.
- **Kalshi API:** Free account. Market data is public (no auth needed). Trading requires RSA-PSS signed requests: generate API key + private key at kalshi.com → Account → Security → API Keys. Base URL: `api.elections.kalshi.com/trade-api/v2`. Game winner series: `KXNCAAMBGAME`. Rate limit: 20 req/sec (Basic tier). Prices are dollar strings (e.g., "0.53" = 53% implied probability).

### Key Assumptions

- We always orient matchups so team_a is the higher seed (lower number). This means `model_prob` is always P(higher_seed_wins).
- Seasons use the spring year convention (2024 = the 2023-24 season).
- We focus on Rounds 1 and 2 only. The architecture supports extending to later rounds.
- The model is intentionally simple (logistic regression). Complexity comes from good features and rigorous validation, not model architecture.
- KenPom data is the primary metrics source for the MVP model. Barttorvik data is ingested and staged but reserved for future use — potential applications include: using Barttorvik's predicted win % as a model feature, using recency-weighted ratings as alternative features, or using Barttorvik predictions as a calibration baseline.

### What NOT To Do

- Do not use random forest, XGBoost, or neural nets. ~624 training observations is not enough to justify complex models. (Confirmed: RF and GBM were tested in Phase 2 experiments and did not improve over logistic regression. See `modeling/EXPERIMENTS.md`.)
- Do not do random train/test splits across years. Always use LOYO CV.
- Do not add features that aren't available pre-tournament (e.g., Round 1 box scores for Round 2 predictions in Option A).
- Do not bet without confirming calibration. A discriminative but miscalibrated model will lose money.
- Do not hard-code the KenPom API key anywhere. Always read from `.env`.
- Do not hammer the KenPom API — add delays between requests and cache responses in DuckDB.
