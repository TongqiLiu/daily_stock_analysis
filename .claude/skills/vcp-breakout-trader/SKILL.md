---
name: vcp-breakout-trader
description: Use this skill when the user asks to analyze or screen breakout trading setups such as VCP, volatility contraction pattern, bull flag, flag consolidation, higher lows, pivot breakout, tight bars, pocket pivot, low-cheat entries, or trader notes like INTC/ACLS/MCHP/ORCL breakout setups. It interprets supply-demand structure, extension risk, volume confirmation, support/pivot levels, stop placement, position sizing, and failure/exit rules for discretionary swing trades.
---

# VCP Breakout Trader

This skill evaluates discretionary breakout setups using the pattern logic shown in the user's examples: VCP contractions, bull flags, higher lows, tight price/volume behavior, and breakout buys near a defined pivot.

## Core Interpretation

- Treat the setup as a supply absorption trade, not a prediction. The trade is valid only when trapped buyers, profit taking, and loss cutting are mostly absorbed before breakout.
- Prefer liquid leaders with strong relative strength, a clean prior advance, and a tight base near highs.
- Avoid chasing stocks that are already extended several ATRs above the relevant moving average unless the user is reviewing an existing position.
- Use the breakout level, last contraction low, and ATR to define risk before discussing upside.
- Downgrade any setup with earnings before the planned hold period unless the user explicitly wants event risk.

For terminology details and grading thresholds, read `references/pattern-rules.md` when needed.

## Workflow

1. **Clarify scope**
   - Extract tickers, timeframe, pattern type, and whether the user wants watchlist analysis, entry planning, or current-position management.
   - If no timeframe is given, default to daily swing trading with a 2-day to 8-week holding window.

2. **Gather current data**
   - Use current market data for price, volume, moving averages, ATR, upcoming earnings, and benchmark context.
   - For user-supplied screenshots, use the image structure first, then verify with current data if the recommendation depends on live prices.

3. **Classify the setup**
   - `VCP`: 2-4 progressively smaller contractions, lower highs or a descending supply line, higher lows, volume dry-up, and a clear pivot.
   - `Bull flag`: strong impulse move followed by 1-5 weeks of shallow downward/sideways consolidation, preferably holding the 10/21 EMA.
   - `Higher-low coil`: at least two higher reaction lows under a stable or descending resistance line.
   - `Breakout already triggered`: price cleared pivot on expanded volume and should now be managed by risk, not chased blindly.

4. **Grade quality**
   - Trend: price above rising 50D and 200D moving averages, or a credible early-stage turn with strong relative strength.
   - Tightness: latest contraction smaller than the first; closes are clustering near the pivot.
   - Volume: volume contracts inside the base and expands on breakout.
   - Extension: entry should be near pivot and preferably no more than 2-3 ATR from the 10/21 EMA.
   - Market regime: broad market and sector should not be in a confirmed risk-off breakdown.

5. **Build a trade plan**
   - Pivot: use the most recent contraction high, descending trendline break, or prior-week high.
   - Entry: prefer buy-stop slightly above pivot or intraday reclaim/low-cheat only when risk is clearly tighter.
   - Stop: place below the final contraction low, flag low, or 1-2 ATR below entry; invalidate if price closes back inside the base on heavy volume.
   - Size: size by max loss, not conviction. Default risk per trade is 0.25%-1.0% of account equity depending on setup grade and market regime.
   - Add: add only after breakout holds and forms a tight secondary shelf; never average down a failed breakout.
   - Exit: take partials or trail if price becomes 3-5 ATR extended from the 10/21 EMA; cut fast on failed breakout.

## Scanner

Use the bundled scanner for a first-pass quantitative check:

```bash
python3 .agents/skills/vcp-breakout-trader/scripts/vcp_breakout_scanner.py \
  --tickers INTC,ACLS,MCHP,ORCL \
  --benchmark QQQ \
  --format markdown
```

The scanner is a filter, not a signal engine. It estimates trend, compression, higher lows, extension, volume, and pivot proximity from daily data. Always review the chart before recommending a trade.

## Output Format

Use this structure:

```markdown
## Strategy Read
- Pattern logic:
- Is it reasonable:
- Main weakness:

## Setup Table
| Ticker | Pattern | Status | Pivot | Support/Stop | Extension | Volume | Grade | Action |

## Ticker Notes
- [Ticker]: setup quality, entry trigger, invalidation level, earnings/event risk.

## Execution Rules
- Entry:
- Stop:
- Add:
- Trim/exit:
- Avoid:

## Bottom Line
- Ranked conclusion and whether to buy now, wait for pivot, manage existing, or skip.
```

Keep recommendations conditional: "valid only above pivot", "skip if already >3 ATR extended", "stop below final contraction low", or "wait because earnings risk dominates".

## Safety

This skill provides trading analysis and workflow guidance, not financial advice. Confirm prices, volume, earnings dates, spreads, and liquidity in the user's broker/charting platform before trading.
