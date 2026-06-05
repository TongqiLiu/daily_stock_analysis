---
name: options-income-scanner
description: Use this skill whenever the user asks for options premium income, 收租, 卖Put, 卖Call, covered call, cash-secured put, wheel strategy, finding stocks to sell puts on, reducing cost basis, screening unusual movers, 大涨/大跌异动, divergence from SPY/QQQ, or stocks where assignment would be acceptable. It scans market/stock divergence, option-chain reward/risk, liquidity, earnings risk, and assignment quality before recommending sell-call or sell-put strikes.
---

# Options Income Scanner

This skill is for practical premium-selling workflows: finding candidates for covered calls and cash-secured puts, then choosing strikes that balance premium, assignment risk, and portfolio intent.

It complements `options-strategy-advisor`: use this skill first to screen candidates and strikes; use `options-strategy-advisor` for deeper Black-Scholes, Greeks, or P/L simulation on a selected trade.

## Core Rules

- Never recommend naked short calls. Sell calls only as covered calls against shares the user owns or explicitly plans to sell.
- Treat selling puts as a conditional stock purchase. Only recommend a put if owning 100 shares at the breakeven price is acceptable.
- Do not confuse high premium with a good trade. High IV caused by earnings, fraud, regulatory, liquidity, or crash risk should usually be filtered out.
- In falling markets, do not sell calls on defensive hedge positions unless the user is willing to trim them. A covered call can weaken the hedge by capping upside.
- Weekly options are high-gamma trades. For 0-14 DTE, reduce size, prefer lower delta, and use limit orders.
- If current data, option chain, earnings date, or liquidity is missing, mark the candidate as incomplete instead of forcing a recommendation.

## Workflow

### 1. Parse Intent

Extract:

- Target universe: user watchlist, holdings, movers, or tickers from a screenshot.
- Side: `sell_put`, `sell_call`, or `both`.
- Expiration: default to the next weekly only if the user says "下周"; otherwise ask or choose 30-45 DTE for standard premium selling.
- Portfolio role: income, willing-to-buy, willing-to-sell, hedge/defensive holding, or short-term trade.
- Assignment constraints: max cash per ticker, whether the user accepts assignment, and stocks they are comfortable owning.

If the user asks for "market anomaly" or "异动", scan both strong positive divergence and strong negative divergence:

- Positive divergence: stock green or outperforming SPY/QQQ while the market is weak. Usually a covered-call or trim candidate, not a new naked-call trade.
- Negative divergence: stock falling more than SPY/QQQ. It is only a sell-put candidate if assignment quality and support are acceptable.

### 2. Gather Data

Use current data. For US listed tickers, prefer live/current sources such as yfinance, broker data, Futu tools, or web finance data.

Collect:

- Current price, day change, 5D/20D trend, volume vs average volume.
- SPY and QQQ benchmark changes for divergence.
- HV20/HV60, ATR14, 20/50/200 day moving averages.
- Recent 20D/60D lows/highs as support and resistance references.
- Next earnings date and ex-dividend date.
- Option chain: bid, ask, last, mid, volume, open interest, IV, and estimated delta/probability.

Use the bundled scanner when helpful:

```bash
python3 .agents/skills/options-income-scanner/scripts/options_income_scanner.py \
  --tickers AAPL,MSFT,NVDA \
  --expiration 2026-06-12 \
  --side both \
  --format markdown
```

When running from `.claude/skills`, use the same relative script in that skill directory.

### 3. Screen for Sell Put Candidates

Prefer puts when all are true:

- The stock is in the user's acceptable assignment universe, or it passes a conservative quality screen.
- The breakeven is near or below a credible support zone, not just slightly under spot.
- The option has acceptable liquidity: OI preferably `>= 100`, volume preferably present, bid/ask spread preferably `<= 15%`; tolerate `<= 30%` only for small size and limit orders.
- No earnings or binary event before expiration unless the user explicitly wants event risk.
- The strike delta fits the user's risk:
  - Conservative: `0.08-0.15`
  - Standard premium selling: `0.16-0.25`
  - Aggressive / willing assignment: `0.25-0.30`
  - Avoid above `0.30` for routine 收租 unless the user wants to buy the stock.

Reject or downgrade:

- Leveraged ETFs for routine assignment (`SOXL`, `TQQQ`, `SQQQ`, `YINN`, `LABU`, etc.).
- Broken downtrends below major moving averages unless the user explicitly wants deep-value assignment.
- Stocks with unresolved accounting, fraud, delisting, liquidity, or regulatory existential risk.
- Puts where premium is high but breakeven remains above obvious support.

### 4. Screen for Covered Call Candidates

Prefer calls when:

- The user owns at least 100 shares or explicitly wants to trim.
- The strike is above the user's desired sell price or above a resistance/expected-move level.
- The premium meaningfully improves the exit price.
- The stock is extended after a strong rally or positively diverging from a weak market.

Choose call delta by portfolio role:

- Defensive/hedge holding: `0.08-0.16`; or do not sell calls.
- Normal covered call income: `0.16-0.30`.
- Willing to sell shares: `0.30-0.45`.

Do not sell near calls just to "降本" if the stock is serving as a hedge in a falling market. In that case, recommend farther OTM calls or no trade.

### 5. Rank by Reward/Risk

For puts, calculate:

- `credit`, `breakeven = strike - credit`
- `cash_required = strike * 100`
- `max_loss = (strike - credit) * 100`
- `return_on_cash = credit / strike`
- `downside_buffer = (spot - breakeven) / spot`
- `probability proxy`: option delta and/or BS probability using chain IV

For calls, calculate:

- `credit`
- `if_called_exit = strike + credit`
- `called_return = (strike - spot + credit) / spot`
- `premium_yield = credit / spot`
- opportunity cost if the stock rallies above strike

Prefer candidates with:

- Reasonable delta, liquid chain, clear management plan.
- Breakeven below support for puts, or strike above resistance for calls.
- Enough premium to justify the capped upside or assignment risk.

### 6. Output Format

Use this structure:

```markdown
## Market Context
- SPY/QQQ:
- Regime:
- Weekly-option warning:

## Best Candidates
| Ticker | Side | Strike | Exp | Credit | Delta | Breakeven/Exit | Buffer/Called Return | Why |

## Candidate Notes
- [Ticker]: support/resistance, divergence, earnings, liquidity, assignment/covered-call logic.

## Avoid / Downgrade
- [Ticker]: reason.

## Execution Plan
- Entry limit:
- Size:
- Profit take:
- Stop / roll:
- Assignment or called-away plan:

## Bottom Line
- Ranked recommendation and whether to trade, wait, or skip.
```

Keep recommendations conditional and explicit: "only if willing to own at X", "only if willing to sell at Y", or "skip because it weakens the hedge".

## Management Rules

- Use limit orders near mid; avoid market orders.
- Close short options at 50-70% of max profit if profit arrives quickly.
- For puts: cut or roll if the thesis breaks, the stock loses key support on volume, or assignment is no longer acceptable.
- For calls: roll up/out if the user wants to keep the shares and the stock approaches the strike.
- Size cash-secured puts by assignment not premium. One contract means readiness to buy 100 shares.

## Disclaimer

This skill provides trading analysis and workflow guidance, not financial advice. Confirm all prices, Greeks, IV, earnings dates, and liquidity in the broker platform before trading.
