# Round 1 Lessons — March Madness 2026

## Final R1 P&L

**Net: -$106.91** on $209.19 deployed. Record: 1W / 13L (TCU only outright winner).

| Team | P&L | Notes |
|------|-----|-------|
| TCU  | +$20.52 | Won outright (8-9 game, model had them at 57%) |
| MCNS | +$3.09  | Profitable — sell break fired at $0.37 (2.5× entry) |
| HOF  | -$7.85  | Sell break at $0.40 recovered $7.60; accidental buy at $0.65 cost $9.75 |
| KENN | -$23.70 | Sell break at $0.20 recovered $10; accidental buy at $0.50 cost $25 |
| All others | -$98.87 | Lost outright, no breaks fired |

---

## Lesson 1: Sell Breaks Only — Never Buy Breaks

R1 had a mix of sell breaks (intentional, correct) and buy breaks (accidental, harmful).

**Break order P&L breakdown:**
- Sell breaks that fired: +$33.14 locked in (MCNS, KENN, HOF)
- Buy breaks that fired: -$51.75 lost (UNI, HOF, CBU, KENN)
- Net break impact: -$18.61
- Unfired sell breaks on CBU/FUR/QUC: correct — capital returned when teams never made a run

**For R2: every break order must be a SELL. No buy breaks.**

A buy break adds exposure at a price where the market has already moved toward or past your model's assessment. If your model says a team has a 16% chance and the market rises to 50% in-game, buying at $0.50 means you're paying 3× fair value. The sell at $0.20 for KENN was correct; the buy at $0.50 gave back everything and more.

---

## Lesson 2: Sell Break Framework

For each underdog position, place 1–2 sell-only break orders pre-game.

### Price levels by entry

| Entry price | Tier 1 (sell 40%) | Tier 2 (sell 30%) |
|-------------|-------------------|-------------------|
| < $0.05     | 4× entry          | model probability |
| $0.05–$0.15 | 3× entry          | model probability |
| $0.15–$0.30 | 2.5× entry        | model probability |
| > $0.30     | skip — not enough upside room | — |

### Quantities
- **Tier 1**: sell 40% of position (fast profit lock, fires more often)
- **Tier 2**: sell 30% of position (exit near fair value if team is genuinely winning)
- **Hold**: remaining 30% rides to settlement (free lottery ticket)

### When to collapse to a single break
- Only set tier 2 if model probability > 1.5× the tier 1 price
- If model prob is close to entry price, skip breaks entirely — not enough spread to be worth it

### Hard caps
- Never set a sell break above $0.75 — unrealistic to hit pre-game
- Round break prices to nearest $0.05 for cleaner order book placement

### R1 validation
- MCNS: entry $0.15, model 31% → tier 1 at ~$0.38 → fired at $0.37 ✓ profitable
- KENN: entry $0.05, model 16.5% → tier 1 at ~$0.15, tier 2 at $0.165 (collapse to single at $0.15) → fired at $0.20, close enough
- HOF: entry $0.15, model 22% → tier 1 at ~$0.38 → fired at $0.40 ✓ recovered value
- CBU: entry $0.10, model 24% → tier 1 at $0.25 → never fired, capital returned ✓
- QUC: entry $0.03, model 13% → tier 1 at $0.12 → never fired, capital returned ✓

---

## Lesson 3: Model Calibration

The KenPom-based model consistently overestimated underdog win probabilities relative to the market. Going 1/13 on settled bets (expected ~2–3 wins given model probs) suggests the market was better calibrated.

**Observed pattern:**
- "High confidence" edges (PENN +16%, MCNS +16.6%) both lost
- Market implied probs of 2–5% for 14–16 seeds were closer to truth than our 9–19% estimates
- The model finds edges everywhere, but the market is liquid and likely reflects information we don't have

**For R2:**
- Apply a skepticism discount to model edges, especially for seeds 13–16
- Treat "medium" confidence edges conservatively; "low" confidence should be sized minimally or skipped
- R2 underdogs are survivors — they won R1, which provides some validation the model edge may be real. This is the one case where the model edge could be *understated*, since the market may not fully credit a team that just pulled an upset.
- Still: run the pessimistic Monte Carlo (model - 5pt haircut) and give it weight alongside base case

---

## Lesson 4: The Break Order Lifecycle

Pre-game placement → in-game trigger → capital returned or profit locked.

- Breaks placed before the game start are limit sell orders. They rest until the in-game market rises to that price.
- When a break fires, it means the underdog is competitive/winning — exactly when you want to take partial profit.
- When a break doesn't fire (price stays low), the team was never competitive and you lose only the initial entry. That's the correct outcome.
- When a market settles, unfired sell breaks are cancelled and capital is returned. There is no residual risk from unfired breaks.

**The in-game market tends to overreact to early underdog leads** (KENN hit $0.50 mid-game against Gonzaga; HOF hit $0.65 mid-game against Alabama). Sell breaks correctly capture this overreaction. Holding to settlement after such a spike is rarely optimal for heavy underdogs.

---

## Lesson 5: Pipeline for R2

The current `forecasting/artifacts/edges_2026.csv` reflects R1 matchups only.

**Before placing any R2 orders:**
```bash
make forecast   # pulls R2 Kalshi matchups → predictions → edges → bet sheet
```

Then review the bet sheet before executing. The agent should:
1. Run `make forecast`
2. Examine `forecasting/artifacts/bet_sheet_2026.csv`
3. Apply the sell break framework above to each recommended bet
4. For each bet, explicitly specify sell break prices and quantities alongside the initial entry
5. Execute with `make execute-live` (per-order confirmation)

---

## Summary Checklist for the R2 Agent

- [ ] Run `make forecast` first — R1 data is stale
- [ ] For each bet: initial buy entry + sell break orders only
- [ ] Sell break tier 1 at 3× entry (40% of position), tier 2 at model prob (30%), hold 30%
- [ ] Adjust tier multiplier by entry price range (see table above)
- [ ] No buy break orders under any circumstances
- [ ] Weight pessimistic sim (model - 5pt) alongside base case when sizing
- [ ] For 13–16 seeds: consider halving the Kelly allocation — R1 calibration suggests model overestimates these
- [ ] For R2 underdogs who won R1: model edge may be more reliable than usual (survivor validation)
