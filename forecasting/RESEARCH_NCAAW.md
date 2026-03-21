# Research Brief: Women's Tournament Pipeline

## Context

We have a working end-to-end March Madness forecasting pipeline for the men's NCAA tournament (NCAAM). It ingests historical data from Kaggle + KenPom + Barttorvik, trains a logistic regression model on team efficiency features, pulls live Kalshi market prices, computes edges, and places bets via the Kalshi API.

We want to evaluate whether we can replicate this pipeline for the women's NCAA tournament (NCAAW) in time for 2026 (Selection Sunday is ~March 16, 2026; first games ~March 21, 2026).

The goal of this research is to identify: (a) what data sources exist for women's college basketball that could substitute for KenPom, (b) whether Kalshi runs women's tournament markets with enough liquidity to bet, and (c) what the realistic build effort looks like given the gaps below.

---

## What the Current Pipeline Uses (Men's)

### Predictive Features (from KenPom)
- `adj_em` — adjusted efficiency margin (points per 100 possessions, adjusted for opponent)
- `adj_o` — adjusted offensive efficiency
- `adj_d` — adjusted defensive efficiency
- `adj_t` — adjusted tempo
- `luck` — luck rating
- Four factors: `efg_pct`, `to_pct`, `orb_pct`, `ftr` (offense and defense versions of each)
- National rank

These are the features fed into the logistic regression model to predict win probability for any matchup. The model was trained on ~14 seasons of men's tournament results.

### Historical Game Data (from Kaggle)
- Kaggle "March Machine Learning Mania" competition provides clean CSVs of historical tournament results, seeds, and team metadata going back to 1985 for men's.
- Women's equivalent exists as a separate Kaggle competition with W-prefixed files.

### Market Data (from Kalshi)
- Men's series ticker: `KXNCAAMBGAME`
- Each contract is a binary: "Will [Team] win [Round]?" priced 0–100
- We pull implied probabilities, compare to our model, and bet where edge > threshold

---

## Research Questions

### 1. KenPom Equivalent for Women's Basketball

KenPom (`kenpom.com`) covers men's Division I only. We need a source that provides:
- Adjusted efficiency margin (or equivalent composite rating)
- Offensive and defensive efficiency, adjusted for opponent
- Ideally: tempo, four factors
- Machine-readable (API, downloadable CSV, or scrapable)
- Historical data going back at least 5–10 seasons

**Candidates to evaluate:**
- **HerHoopStats** (`herhoopstats.com`) — does it publish adjusted efficiency metrics? Is there an API or bulk download? How many seasons of history?
- **ESPN BPI (Women's)** — does ESPN publish a women's Basketball Power Index? Is it accessible programmatically?
- **Her Hoop Stats / NCAA Stats Portal** — what does the NCAA publish at `stats.ncaa.org` for women's? Box scores? Team efficiency?
- **Torvik / cbbdata** — does `cbbdata.com` (used for men's via the `cbbdata` R/Python package) cover women's basketball? Is there a `cbbwdata` or equivalent?
- **Massey Ratings / Sagarin** — do these publish women's college basketball ratings?
- **Sports-reference (sports-reference.com/cbb/women)** — what efficiency data is available? Is it structured enough to use as a training signal?
- **Any other sources** — what do serious NCAAW forecasters (e.g., FiveThirtyEight successors, CollegeBasketballReference) use?

For each source, assess:
- What metrics are available
- How many seasons of history
- Whether there is a free API, paid API, or scraping required
- Whether the data is clean / standardized enough to use as ML features
- License / terms of service considerations

### 2. Kaggle Women's Dataset

The Kaggle "March Machine Learning Mania" competition has a women's counterpart. Research:
- What is the exact Kaggle competition slug/URL for the women's dataset?
- What W-prefixed CSV files are included? (We need at minimum: `WNCAATourneyCompactResults`, `WNCAATourneySeeds`, `WTeams`)
- How many seasons of tournament history are available?
- Is the data format identical to the men's dataset (same column names, same DayNum convention)?
- What are the DayNum ranges for women's tournament rounds? (We need this to fix our round-mapping SQL — men's uses 136-137 for R64, 138-139 for R32, etc.)

### 3. Kalshi Women's Markets

Research whether Kalshi runs NCAA Women's Tournament markets:
- Does Kalshi have a women's basketball series? What is the series ticker? (Men's is `KXNCAAMBGAME`)
- How many markets are typically listed? (Men's has ~63 game contracts per tournament)
- What is the typical bid/ask spread on women's markets vs. men's?
- What is the typical open interest / volume on women's markets?
- Is liquidity sufficient to place $50–$500 orders without significant slippage?
- Are the contract structures identical to men's (binary win/lose per game per round)?

You can check Kalshi's public API (no auth required) at:
- Series list: `https://api.elections.kalshi.com/trade-api/v2/series`
- Markets by series: `https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXNCAAMBGAME` (substitute women's ticker)

### 4. Feasibility Assessment

Given the above findings, assess:
- **Is there a women's equivalent of KenPom with enough history (5+ seasons) to train a logistic regression model?**
- **If yes:** What is the realistic effort to build an ingestion script, retrain the model, and extend the pipeline? (We're a solo developer with the men's pipeline already working.)
- **If no:** Is there a simpler approach — e.g., using seed number alone as a proxy (no efficiency features), or using public consensus win probabilities from FiveThirtyEight / ESPN as the "model"?
- **Are Kalshi women's markets liquid enough to be worth betting?** If the spread is 10+ points wide and volume is thin, the edge may not be extractable even with a good model.

---

## Deliverables

Please provide:

1. **Data source recommendation** — the single best KenPom substitute for women's basketball, with justification (metrics available, history depth, accessibility)
2. **Kaggle dataset details** — competition URL, file list, DayNum round ranges
3. **Kalshi women's market snapshot** — series ticker (if it exists), market count, typical spread, liquidity verdict
4. **Go/no-go recommendation** — given data availability and Kalshi liquidity, is it worth building the women's pipeline for 2026? Or is it better to revisit for 2027 when we have more lead time?

---

## Out of Scope

- Building the actual pipeline (that comes after this research)
- Scraping any site in violation of its ToS
- Anything requiring authentication to research (focus on public data)
