---
name: options-portfolio-risk-manager
description: Use this skill when the user asks to manage or review an existing stock, ETF, leveraged ETF, covered-call, cash-secured put, short option, long option, or mixed options portfolio; asks whether to close, roll, accept assignment, sell calls after assignment, wheel a position, reduce assignment risk, estimate option exercise/touch probability, assess leveraged ETF decay, or rank which current positions need action first. It is for portfolio-level risk control and trade management, not for screening brand-new premium candidates.
---

# Options Portfolio Risk Manager

This skill reviews existing stock and options portfolios with assignment avoidance, quality-of-underlying, rolling, covered-call, and leveraged-ETF decay discipline.

It complements sibling options skills:

- Use `options-income-scanner` for screening new sell-put/sell-call candidates before opening a trade.
- Use `options-strategy-advisor` for deeper Black-Scholes, Greeks, payoff, or multi-leg strategy simulation.
- Use `options-review-logger` when the task is mainly importing broker screenshots, Excel records, or trade journals.

This skill is unrelated to `src/services/portfolio_risk_service.py`, which is a DSA product service for portfolio concentration and drawdown alerts.

## Default User Policy

Unless the user gives different rules, use this conservative income policy:

- Prefer not to be assigned.
- Prefer underlyings in a range-bound or constructive trend, not a one-way downtrend. **Trend check is mandatory** — see `options-income-scanner` →「单边下跌趋势（收租硬性风控）」.
- **Event calendar check is mandatory** before new short options — see `options-income-scanner` →「关键事件节点（收租硬性风控）」; roll or close positions that will cross earnings / FOMC / major macro unless user accepts event risk.
- Prefer fundamentally sound, liquid companies where owning 100 shares at breakeven is acceptable.
- Treat assignment as a wheel/covered-call or long-hold plan only when quality and trend support it.
- Treat weekly options as high-gamma risk; reduce size and roll early when short-put delta rises.
- Do not recommend adding risk just to recover a loss.

## Workflow

1. **Parse the portfolio**
   - Read user text, screenshots, or `positions.yaml`.
   - Separate stock/ETF holdings, leveraged ETFs, short puts, short calls, covered calls, long options, and defined-risk short spreads.
   - Use bundled spread support for `short_put_spread` and `short_call_spread`; pass iron condors, diagonals, ratio spreads, or portfolio-margin cases to `options-strategy-advisor` or a broker risk engine.
   - If contracts are missing, assume 1 contract and state that assumption.

2. **Fetch current data**
   - The bundled scripts currently use yfinance for US listed equities and options.
   - If broker/Futu data is available in the session, use it to supplement or override stale/missing yfinance fields and state the source.
   - Collect spot, option chain, bid/ask/mid, IV, OI, volume, DTE, 20/50/200D trend, HV20/HV60, 20D/60D highs/lows.
   - Mark missing data explicitly; do not force a precise recommendation when live chain data is missing.

3. **Score each short option**
   - For short puts: calculate delta, probability ITM, rough touch probability, breakeven, assignment cash, support buffer, trend quality, and liquidity.
   - For short calls: calculate call-away probability, capped upside, and whether the user is willing to sell the shares.
   - For covered calls: pair same-symbol stock shares with short calls; flag uncovered shares as high risk.
   - For short spreads: calculate short-leg probability, spread mark, defined max loss, max profit, and event/gamma risk; do not treat max assignment cash as the primary risk.
   - For long options: score time-decay pressure, OTM status, mark-vs-cost P/L, and urgent exit/roll windows.
   - Flag short puts above conservative delta, inside 14 DTE, below broken support, or on weak-quality underlyings.

4. **Aggregate underlying exposure**
   - Combine same-underlying stock shares, option delta shares, leveraged ETF underlying-equivalent shares, naked put assignment cash, defined-risk spread max loss, and covered-call call-away shares.
   - Use this section to identify true concentration, such as MSTR short puts plus MSTX underlying-equivalent shares.

5. **Rank action priority**
   - Priority 1: near-expiry short options that can force unwanted assignment.
   - Priority 2: large leveraged ETF positions in a broken trend.
   - Priority 3: ITM or high-delta longer-dated short options that consume margin or lock in directional risk.
   - Priority 4: defined-risk spreads near max loss, uncovered calls, and long options with urgent theta risk.
   - Priority 5: covered-call candidates and routine profit-taking.

6. **Evaluate roll choices**
   - Run `scripts/option_roll_screener.py` for each high-risk short option flagged by `portfolio_risk.py`.
   - Pass `--policy` when available so roll DTE, delta, OI, and spread filters match the user's conservative settings.
   - Prefer rolling short puts down and out for credit or small debit only when the underlying remains acceptable.
   - If the underlying quality or trend is broken, prefer closing or converting to defined risk over rolling indefinitely.

7. **Evaluate leveraged ETF decay**
   - Run `scripts/leveraged_etf_decay.py` for daily-reset ETFs such as TQQQ, SOXL, MSTX, MSTU, LABU, FNGU when the user may hold beyond a short trade.
   - Treat leveraged ETFs as path-dependent instruments; long holding periods require a strong directional trend, not just eventual recovery.

8. **Output a management plan**
   - Start with the top action item.
   - Give a table of positions, risk flags, and recommended handling.
   - Include exact price/strike/date assumptions.
   - Keep recommendations conditional: "roll only if willing to own at breakeven", "sell call only if willing to cap recovery", "close if assignment is unacceptable".
   - Include "For educational analysis only, not financial advice"; require broker confirmation for prices, Greeks, liquidity, and corporate events.

## Scope and Limits

- Default market scope is US listed equities, ETFs, and listed equity options.
- HK options, warrants, CBBCs, or broker-only products require live broker/Futu or Longbridge data; yfinance chain quality is not enough.
- Do not use this skill as the primary tool for finding brand-new premium trades. Use `options-income-scanner` first, then return here after a position exists.
- Do not rely on the bundled scripts for complex spreads, iron condors, diagonals, ratio spreads, or portfolio margin modeling; use `options-strategy-advisor` or a broker risk engine.
- Treat long-option scoring as a time-decay management aid, not as a full scenario P/L simulation.

## Orchestration Checklist

1. Run `portfolio_risk.py` first for mixed portfolios.
2. For each high-risk `short_put` or `short_call`, run `option_roll_screener.py`.
3. Review `Underlying Exposure` before deciding size; concentration can be larger than any single leg implies.
4. For each `leveraged_etf` held beyond a trade, run `leveraged_etf_decay.py`.
5. Combine the script outputs into one management plan with top action, do/skip conditions, and watch levels.
6. State missing data and assumptions instead of presenting false precision.

## Tools

Set the skill directory first. In the local Codex mirror use:

```bash
SKILL_DIR=.agents/skills/options-portfolio-risk-manager
```

In the repository canonical skill path use:

```bash
SKILL_DIR=.claude/skills/options-portfolio-risk-manager
```

### Portfolio Risk

Run:

```bash
python3 "$SKILL_DIR/scripts/portfolio_risk.py" \
  --positions "$SKILL_DIR/references/positions.example.yaml" \
  --policy "$SKILL_DIR/references/risk_policy.example.yaml" \
  --format markdown
```

Use this first when the user gives multiple holdings or wants an overall risk read. It outputs a portfolio read, position table, underlying exposure table, follow-up checks, notes, and disclaimer. It does not automatically append full roll or decay scenario tables.

### Roll Screener

Run:

```bash
python3 "$SKILL_DIR/scripts/option_roll_screener.py" \
  --symbol MSTR \
  --side short_put \
  --expiry 2026-06-26 \
  --strike 103 \
  --policy "$SKILL_DIR/references/risk_policy.example.yaml"
```

Use this when a short option is threatened or the user asks whether to roll.

### Leveraged ETF Decay

Run:

```bash
python3 "$SKILL_DIR/scripts/leveraged_etf_decay.py" \
  --leverage 2 \
  --vols 0.5,0.75,1.0 \
  --years 1,2,4 \
  --underlying-multiples 1,1.5,2,3,4
```

Use this for daily leveraged ETFs and for "can I hold this for 1/2/4 years" questions.

## Config Templates

- `references/positions.example.yaml`: portfolio input template.
- `references/risk_policy.example.yaml`: conservative assignment-avoidance policy template.

The scripts support JSON and the simple YAML structure used by the templates. They also use `yaml.safe_load` automatically if PyYAML is installed.

Policy field usage:

- `max_weekly_dte`, short-put delta thresholds, OI, spread, market-cap, HV, and covered-call delta thresholds feed `portfolio_risk.py`.
- `max_shares` flags short-put assignment size when total potential assigned shares exceed the user's preferred ticker limit.
- `event_risk_dte` upgrades near-DTE short options that cross earnings or ex-dividend dates.
- `long_option_exit_dte` and `long_option_urgent_dte` score long-option time-decay pressure.
- `roll_dte_min`, `roll_dte_max`, short-put conservative delta, OI, and spread thresholds feed `option_roll_screener.py` when `--policy` is supplied.

## Output Shape

`portfolio_risk.py` output:

```markdown
## Portfolio Read
- Top action:
- Total assignment cash:
- Assignment cash within 14 DTE:

## Position Table
| Symbol | Position | Spot | Mark | Delta | DTE | Breakeven | Assignment Cash | Max Loss | Events | Risk | Action |

## Underlying Exposure
| Underlying | Spot | Stock Shares | Option Delta Sh | Leveraged Eq Sh | Net Delta Sh | Assignment Cash | Defined-Risk Max Loss | Call-Away Sh | Risk | Notes |

## Follow-up Checks
- Run roll screener for...
- Run leveraged ETF decay for...

## Notes
- Position-specific notes.

## Disclaimer
- For educational analysis only...
```

Final answer after running required follow-ups:

```markdown
## Portfolio Read
- Top action:
- Main risk:
- Data gaps:

## Position Table
| Symbol | Position | Risk | Action | Why |

## Underlying Exposure
| Underlying | Net Delta Sh | Assignment Cash | Defined-Risk Max Loss | Call-Away Sh | Risk |

## Roll / Repair Candidates
| Current | Candidate | Net Credit/Debit | New Delta | New Breakeven | Comment |

## Leveraged ETF Decay
| ETF | Horizon | Assumed Vol | Decay | Comment |

## Plan
1. First action...
2. Second action...
3. Monitoring level...
```

## Disclaimer

This skill provides trading analysis and workflow guidance, not financial advice. Confirm all prices, Greeks, IV, earnings dates, ex-dividend dates, borrow/liquidity, margin impact, and order execution in the broker platform before trading.
