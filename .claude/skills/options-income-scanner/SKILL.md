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

## 单边下跌趋势（收租硬性风控）

**后续凡做期权策略分析（卖 Put / 卖 Call / 收租 / wheel），必须先判断趋势 regime；命中单边下跌时，优先级高于「IV 高、权利金厚」。**

### 什么是单边下跌趋势

同时满足 **≥2 条** 即视为单边下跌（`downtrend = true`）：

- 现价低于 **MA20 且 MA50**（最好也标注与 MA200 关系）
- **20 日或 60 日**涨跌幅为负，且**明显弱于 SPY/QQQ**（例如 60 日跑输大盘 ≥10pp）
- 价格结构：**lower high / lower low**，或 60 日内从高位回撤 **≥15–20%**
- 卖 Put 场景下：现价接近或跌破 **20D/60D 低点**，而非在支撑上方企稳

**不是**单边下跌的情况（可继续正常评估，但仍看 IV / 接货意愿）：

- 宽幅震荡：涨跌互现，均线附近缠绕
- 大盘同步回调，但个股相对抗跌或仅小幅跑输
- 单日大跌后的反弹修复期，且未再创新低

### 对卖 Put / 收租的含义（硬性）

| 场景 | 默认动作 |
|---|---|
| 单边下跌 + **常规收租**（不想接股） | **Skip / 不推荐**；高 IV 权利金是风险补偿，不是 alpha |
| 单边下跌 + 用户**明确愿意接货** | 仅当 strike 在**可接受接货价**且 breakeven **低于**关键支撑；缩小仓位，DTE 不宜过短 |
| 单边下跌 + **高 Beta / 杠杆代理**（如 MSTR、BTC 相关） | **默认 Skip 卖 Put**；已有持仓优先 `options-portfolio-risk-manager`（roll / 接股后卖 Call 降本） |
| 单边下跌 + 已有正股 | 优先 **远价 Covered Call** 或等待反弹至 MA20/MA50 再卖 Call，**不在低点卖近价 Call 抢权利金** |

### 与 IV 的关系（用户实盘教训）

- **低～中 IV 质量股**（如 BRK.B、MCD）：趋势友好时更适合稳定收租。
- **高 IV 不等于好卖 Put**：MSTR 类标的 IV/HV 常 **>70%**，权利金看似诱人，但单边下跌中极易价内指派，一次亏损可抵消多周收租。
- 分析输出中必须分开写：**「IV 环境」** 与 **「趋势 regime」**；二者冲突时 **以趋势为准**。

### 输出中必须体现

在 `## Market Context` 增加：

- `Trend regime`: `uptrend` / `range` / **`one-way downtrend`** / `unclear`
- `vs SPY 20D/60D`: 相对强弱
- 若 `one-way downtrend`：`## Avoid / Downgrade` 中写明**禁止/降级卖 Put 的原因**，即使 scanner 扫出高 premium candidate

## 关键事件节点（收租硬性风控）

**后续凡做期权策略分析，除趋势外必须核对「事件日历」；事件风险优先级与单边下跌同级，高于权利金吸引力。**

目标：避开财报、美联储会议、重大宏观数据等可能带来的 **IV 飙升 + 缺口波动（gap）**，防止卖方被瞬间打穿 strike。

### 必须收集的事件

| 类型 | 典型例子 | 数据来源 |
|---|---|---|
| **个股财报** | Earnings date | yfinance `earningsDate`、券商、FMP |
| **美联储** | FOMC 决议、主席讲话 | 财经日历、fed.gov |
| **美国宏观** | CPI、PPI、非农 NFP、GDP、PCE | 财经日历 |
| **除息** | Ex-dividend | yfinance；价内 Call 有被 early exercise 风险 |
| **个股二元事件** | FDA、产品发布、监管听证、股东大会 | 新闻 / 用户补充 |
| **市场结构** | 四重到期 OPEX（季度） | 日历；短 DTE 卖方 Gamma 更高 |

查不到日期时标 `event_calendar: incomplete`，**常规收租默认 Skip 或降级**，不要假装无事件风险。

### 默认缓冲规则（常规卖 Put / Covered Call 收租）

以**计划持仓期**（开仓日 → 到期日）为准：

| 事件 | 默认动作 |
|---|---|
| **财报在到期日之前**（持仓期内含财报） | **不推荐新开卖方**；除非用户明确要做 earnings play |
| **财报在到期后 1–2 个交易日内** | **降级**；周权（≤14 DTE）默认 Skip |
| **FOMC / CPI / NFP 在持仓期内** | 指数敏感仓、高 Beta、科技股：**周权默认 Skip**；防御股（BRK、MCD）可降级为远价 + 小仓 |
| **除息日在到期前且 Call 价内/近价** | Covered Call 需提示 **early assignment**；不确定则 Skip 近价 Call |
| **已知 FDA / 监管 / 产品发布在持仓期内** | 小盘 / 生物医药 / 高波动：**默认 Skip** |

**经验法则**：收租要的是 **theta + 低 realized move**；任何可能让股价单日波动 **>1×ATR14 甚至 gap** 的节点，都不适合常规卖方。

### 与用户意图的关系

| 用户意图 | 事件窗内是否可做 |
|---|---|
| **常规收租**（默认） | 持仓跨事件 → **Skip**；或换到期日到事件**之前**到期（留 1–2 天缓冲） |
| **愿意赌财报/事件** | 仅当用户**明确声明**；转 `options-strategy-advisor` 做 straddle/strangle 等买方或定义风险结构，**不是**裸卖 Put/Call |
| **已有持仓跨事件** | 转 `options-portfolio-risk-manager`：平仓 / roll 到事件后 / 缩仓 |

### 输出中必须体现

在 `## Market Context` 增加：

- `Event calendar`: 列出持仓期内 **Earnings / FOMC / CPI / NFP / Ex-div**（无则写 `none in window`）
- `Event risk`: `clear` / `elevated` / **`blocked`**
- 若 `blocked`：在 `## Avoid / Downgrade` 写明**具体事件 + 日期 + 建议**（换到期日 / 等事件后 / Skip）

```markdown
## Event Calendar (hold window)
- Earnings: [date or none before expiry]
- Macro: [FOMC/CPI/NFP dates in window or none]
- Ex-div: [date if relevant]
- Event risk: blocked | elevated | clear
```

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
- **Event calendar for the planned hold window** (open → expiry): earnings, FOMC, CPI/PPI/NFP, ex-div, and any ticker-specific binary events the user cares about.
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
- **No key macro event (FOMC / CPI / NFP) inside the hold window** for routine weekly premium selling unless user accepts event risk and size is reduced.
- The strike delta fits the user's risk:
  - Conservative: `0.08-0.15`
  - Standard premium selling: `0.16-0.25`
  - Aggressive / willing assignment: `0.25-0.30`
  - Avoid above `0.30` for routine 收租 unless the user wants to buy the stock.

Reject or downgrade:

- Leveraged ETFs for routine assignment (`SOXL`, `TQQQ`, `SQQQ`, `YINN`, `LABU`, etc.).
- **单边下跌趋势**（见上文硬性风控）：常规收租 **一律不推荐卖 Put**；高 IV 仅作警告，不作开仓理由。
- **关键事件节点**（见「关键事件节点」）：持仓跨财报 / FOMC / 重大宏观 → 常规收租 **Skip** 或换到期到事件前。
- Broken downtrends below major moving averages unless the user explicitly wants deep-value assignment.
- High-beta / levered proxies in downtrends (`MSTR`, meme/high-HV small caps) for **new** short puts.
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
- Trend regime: (uptrend / range / one-way downtrend / unclear)
- vs SPY 20D/60D:
- IV vs trend note: (若 IV 高但单边下跌，明确写「权利金陷阱」)
- Event calendar / Event risk: (clear / elevated / blocked)
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
