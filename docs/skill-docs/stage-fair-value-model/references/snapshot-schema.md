# Snapshot Schema

先收集结构化快照，再做文字分析。默认支持 `PE`、`EV/Sales` 与 `development EV/Sales` 三套输入。

## PE Snapshot Example

```json
{
  "symbol": "AMKR",
  "companyName": "Amkor Technology",
  "asOf": "2026-05-15",
  "valuationMode": "auto",
  "valuationBasisType": "independent_fair_value",
  "valuationBasis": "independent peer/history/stage multiple assumptions",
  "multipleFramework": "custom_framework",
  "currentPrice": 77.22,
  "marketCap": 19270000000,
  "stage": "stage-2",
  "theme": "先进封装 / OSAT / AI 供应链",
  "riskFlags": [
    "cyclical_peak_eps_risk"
  ],
  "scenarios": {
    "bear": {
      "label": "FY2026 low",
      "eps": 2.19,
      "multiple": 29.2
    },
    "base": {
      "label": "FY2027 avg",
      "eps": 2.57,
      "multiple": 36.5
    },
    "bull": {
      "label": "FY2027 high",
      "eps": 4.23,
      "multiple": 45.8
    }
  },
  "analystTargets": {
    "average": 73.17,
    "high": 90.0,
    "low": 60.0
  },
  "options": {
    "putCallVolumeRatio": 0.35,
    "putCallOiRatio": 0.95
  },
  "historicalPriceRanges": {
    "3m": {
      "startDate": "2026-02-16",
      "endDate": "2026-05-15",
      "tradingDays": 63,
      "low": 58.40,
      "median": 70.25,
      "high": 82.10,
      "historyMayBeStale": false
    }
  },
  "news": [
    "一句话写最近财报/政策/产业催化"
  ],
  "sources": {
    "price": "url",
    "eps": "url",
    "analystTargets": "url",
    "options": "url",
    "news": ["url1", "url2"]
  }
}
```

## EV/Sales Snapshot Example

```json
{
  "symbol": "FLNC",
  "companyName": "Fluence Energy",
  "asOf": "2026-05-15",
  "valuationMode": "auto",
  "valuationBasisType": "independent_fair_value",
  "valuationBasis": "independent EV/Sales assumptions from guidance, peers, and stage",
  "multipleFramework": "energy_storage_scaled_ev_sales",
  "currentPrice": 20.77,
  "marketCap": 3830000000,
  "netDebt": -103000000,
  "dilutedSharesOutstanding": 184400000,
  "stage": "stage-1-2",
  "theme": "储能 / 数据中心电力需求 / backlog 重估",
  "scenarios": {
    "bear": {
      "label": "FY2026 low revenue",
      "revenue": 3200000000,
      "multiple": 0.9
    },
    "base": {
      "label": "FY2026 mid revenue",
      "revenue": 3400000000,
      "multiple": 1.2
    },
    "bull": {
      "label": "FY2026 high revenue",
      "revenue": 3600000000,
      "multiple": 1.6
    }
  }
}
```

## Development EV/Sales Snapshot Example

用于 OKLO 这类开发阶段企业：近端 EPS 为负、近端收入很小，但 2-5 年远期收入预测可得。

```json
{
  "symbol": "OKLO",
  "companyName": "Oklo Inc.",
  "asOf": "2026-05-16",
  "valuationMode": "auto",
  "developmentStage": true,
  "valuationBasisType": "market_implied_sensitivity",
  "valuationBasis": "FY2028 revenue consensus and current implied future EV/Sales sensitivity",
  "multipleFramework": "development_market_implied_ev_sales",
  "forecastFiscalYear": "FY2028",
  "currentPrice": 62.25,
  "marketCap": 10830000000,
  "netDebt": -2470000000,
  "dilutedSharesOutstanding": 173870000,
  "stage": "stage-1",
  "theme": "先进核能 / Aurora Powerhouse / AI 数据中心电力需求",
  "scenarios": {
    "bear": {
      "label": "FY2028 low revenue",
      "revenue": 100180000,
      "multiple": 50
    },
    "base": {
      "label": "FY2028 consensus revenue",
      "revenue": 105520000,
      "multiple": 80
    },
    "bull": {
      "label": "FY2028 high revenue",
      "revenue": 110860000,
      "multiple": 120
    }
  }
}
```

## Development LTM Implied Snapshot Example

用于 XE 这类新上市开发阶段企业：无卖方目标价、无公开前瞻收入一致预期，只能做当前隐含倍数敏感性。

```json
{
  "symbol": "XE",
  "companyName": "X-Energy, Inc.",
  "asOf": "2026-05-15",
  "valuationMode": "auto",
  "developmentStage": true,
  "developmentScenarioPolicy": "ltm_implied_multiple_sensitivity",
  "valuationBasisType": "market_implied_sensitivity",
  "valuationBasis": "LTM revenue and current implied EV/Sales sensitivity",
  "multipleFramework": "development_market_implied_ev_sales",
  "currentPrice": 27.30,
  "marketCap": 10710000000,
  "netDebt": -740610000,
  "dilutedSharesOutstanding": 392350000,
  "ltmRevenue": 94260000,
  "stage": "stage-1",
  "theme": "SMR / Xe-100 / TRISO-X fuel / AI data center power demand"
}
```

## Required

- `symbol`
- `asOf`
- `currentPrice`
- `scenarios.*.multiple`，除非使用 `multipleFramework` 或 `developmentScenarioPolicy = ltm_implied_multiple_sensitivity`
- 以及以下两组之一：
  - `PE` 路径：
    - `scenarios.bear.eps`
    - `scenarios.base.eps`
    - `scenarios.bull.eps`
  - `EV/Sales` 路径：
    - `scenarios.bear.revenue`
    - `scenarios.base.revenue`
    - `scenarios.bull.revenue`
    - `netDebt`
    - `dilutedSharesOutstanding`

## Recommended

- `companyName`
- `marketCap`
- `valuationMode`
- `developmentStage`
- `developmentScenarioPolicy`
- `valuationBasisType`
- `valuationBasis`
- `multipleFramework`
- `forecastFiscalYear`
- `theme`
- `riskFlags`
- `analystTargets`
- `options`
- `historicalPriceRanges`
- `valuationFamilies`
- `stageMultipleSensitivity`
- `news`
- `sources`

## Valuation Basis Type

`valuationBasisType` 用来区分“独立合理价”和“市场隐含压力测试”：

- `independent_fair_value`：倍数来自历史区间、同行区间、阶段规则或基本面质量判断。脚本会输出 `valuationBand`，可用于估值评级。
- `market_implied_sensitivity`：倍数来自当前股价反推的 forward PE、EV/Sales 或 LTM implied multiple。脚本会输出 `valuationSignal = not_independent`，`valuationBand = null`，只保留 `sensitivityBand`。
- `hybrid`：混合独立和隐含假设。脚本会输出 `valuationSignal = mixed_basis`，正文必须拆成两段解释。

基准合理价不要直接使用当前隐含倍数。如果使用当前隐含倍数，必须设置 `valuationBasisType = market_implied_sensitivity`。

## Model Confidence and Risk Flags

脚本会输出：

- `qualityWarnings`：结构化质量警报，包含 `code / severity / message`
- `modelConfidence`：`high / medium / low`
- `modelConfidenceScore`：0-100 分，便于批量回测
- `modelUse`：当前输出适合当独立估值、方向性估值、市场隐含压力测试还是低置信诊断
- `guardrailedValuationSignal`：最终报告里优先引用的估值信号

推荐在输入里填写 `riskFlags`：

```json
{
  "riskFlags": [
    "cyclical_peak_eps_risk",
    "margin_path_unproven",
    "early_project_execution_risk",
    "current_revenue_cannot_explain_market_cap",
    "dilution_or_financing_risk"
  ]
}
```

常用值：

| riskFlag | 适用场景 |
|---|---|
| `cyclical_peak_eps_risk` | EPS 可能处在周期峰值，必须用中周期盈利复核 |
| `current_revenue_cannot_explain_market_cap` | 当前收入无法解释市值，模型应转向反向估值 |
| `reverse_valuation_primary` | 主要结论来自当前价反推预期 |
| `margin_path_unproven` | EV/Sales 需要利润率兑现才能成立 |
| `early_project_execution_risk` | 收入来自项目、监管、发射、产能或客户里程碑 |
| `dilution_or_financing_risk` | 未来融资或股本稀释会影响每股价值 |
| `hardware_rerating_or_margin_path_required` | 硬件/光模块/网络设备估值依赖重新评级或利润率上行 |

使用规则：

- `modelConfidence = high`：可引用 `guardrailedValuationSignal`
- `modelConfidence = medium`：只能写方向性估值，必须披露警报
- `modelConfidence = low`：不得直接写“买入 / 便宜 / 昂贵”，只能写诊断、反向估值或观察清单
- `valuationBasisType = market_implied_sensitivity` 时，`guardrailedValuationSignal` 固定不是独立估值信号

## Multiple Framework

`multipleFramework` 用来固定倍数假设，提高复现性。能匹配框架时必须填写；无法匹配才用 `custom_framework`。

| multipleFramework | 模式 | 熊 | 基准 | 牛 |
|---|---|---:|---:|---:|
| `traditional_memory_cycle_pe` | PE | 5x | 7x | 9x |
| `ai_memory_upcycle_pe` | PE | 8x | 10x | 12x |
| `cyclical_semiconductor_pe` | PE | 6x | 8x | 10x |
| `mature_semiconductor_pe` | PE | 18x | 25x | 32x |
| `premium_ai_platform_pe` | PE | 20x | 25x | 30x |
| `mega_cap_ai_cloud_pe` | PE | 20x | 28x | 36x |
| `mature_quality_software_pe` | PE | 18x | 23x | 28x |
| `consumer_platform_pe` | PE | 20x | 25x | 30x |
| `mega_cap_ad_platform_pe` | PE | 18x | 23x | 28x |
| `premium_semiconductor_ip_pe` | PE | 45x | 65x | 85x |
| `high_growth_profitable_pe` | PE | 35x | 50x | 70x |
| `financial_hybrid_pe` | PE | 12x | 18x | 24x |
| `payment_fintech_pe` | PE | 18x | 25x | 32x |
| `premium_fintech_platform_pe` | PE | 25x | 35x | 45x |
| `cyclical_midcycle_pe` | PE | 6x | 8x | 10x |
| `networking_platform_pe` | PE | 25x | 35x | 45x |
| `optical_component_cycle_pe` | PE | 18x | 25x | 32x |
| `ai_power_profit_pe` | PE | 16x | 22x | 28x |
| `ai_power_growth_pe` | PE | 25x | 35x | 45x |
| `defensive_consumer_pe` | PE | 18x | 22x | 26x |
| `defensive_healthcare_pe` | PE | 14x | 17x | 20x |
| `defensive_restaurant_pe` | PE | 20x | 24x | 28x |
| `regulated_utility_pe` | PE | 18x | 22x | 26x |
| `storage_integrator_ev_sales` | EV/Sales | 0.5x | 0.8x | 1.2x |
| `scaled_growth_ev_sales` | EV/Sales | 3x | 5x | 8x |
| `energy_storage_scaled_ev_sales` | EV/Sales | 0.8x | 1.2x | 1.8x |
| `ai_data_center_power_storage_ev_sales` | EV/Sales | 1.2x | 2.0x | 3.0x |
| `ai_chip_hardware_ev_sales` | EV/Sales | 10x | 15x | 25x |
| `ai_infra_growth_ev_sales` | EV/Sales | 25x | 40x | 60x |
| `post_ipo_scarcity_premium_ev_sales` | EV/Sales | 60x | 85x | 120x |
| `current_implied_base_revenue_sensitivity` | EV/Sales | implied ×0.7 | implied ×1.0 | implied ×1.4 |
| `space_defense_scaled_ev_sales` | EV/Sales | 5x | 8x | 12x |
| `early_project_ramp_ev_sales` | EV/Sales | 15x | 25x | 35x |
| `ai_power_scaled_ev_sales` | EV/Sales | 12x | 20x | 30x |
| `development_market_implied_ev_sales` | development EV/Sales | implied ×0.425 | implied ×0.85 | implied ×1.50 |

如果 `scenarios.*.multiple` 缺失，脚本会根据 `multipleFramework` 自动补齐。若手填倍数覆盖框架值，脚本会保留手填值并输出 warning。

## Valuation Families

`valuationFamilies` 是默认推荐输入，用来输出“分类口径 × 熊 / 基准 / 牛”的表。它不改变主模型的 `valuationSignal`，只解释不同业务分类会怎样影响价格带。

最小格式：

```json
{
  "valuationFamilies": [
    {
      "classification": "storage_integrator_ev_sales",
      "framework": "storage_integrator_ev_sales",
      "role": "conservative",
      "applicability": "低毛利储能硬件 / 项目集成商",
      "explanation": "保守下沿"
    },
    {
      "classification": "energy_storage_scaled_ev_sales",
      "framework": "energy_storage_scaled_ev_sales",
      "role": "main_model",
      "applicability": "规模化储能平台，backlog 放量但盈利仍未完全站稳",
      "explanation": "主模型"
    },
    {
      "classification": "ai_data_center_power_storage_ev_sales",
      "framework": "ai_data_center_power_storage_ev_sales",
      "role": "sentiment_upper",
      "applicability": "被当成 AI 数据中心电力 / 储能基础设施重估",
      "explanation": "叙事上限",
      "basisType": "market_sentiment_sensitivity"
    }
  ]
}
```

脚本规则：

- 每个 family 默认复用主 `scenarios` 的 EPS 或 revenue。
- `framework` 命中固定框架时，脚本自动填三档倍数。
- 无固定框架时，填 `multiples.bear/base/bull`。
- `basisType = market_sentiment_sensitivity` 表示情绪 / 稀缺性上限，不是独立合理价。
- `current_implied_base_revenue_sensitivity` 使用当前价和基准收入反推 EV/Sales，再乘以 0.7 / 1.0 / 1.4；只能作为压力测试。

## Stage Multiple Sensitivity

`stageMultipleSensitivity` 是可选附表，用来复现或对比外部“阶段倍数”模型。它只回答：如果市场套用通用 Stage PE 倍数，价格敏感性会到哪里。它不会改变主模型的 `valuationBand`、`valuationSignal` 或最终估值评级。

支持的框架：

| framework | 阶段 | 熊 | 基准 | 牛 |
|---|---|---:|---:|---:|
| `stage_2_rapid_growth_pe` | Stage 2 rapid growth | 29.2x | 36.5x | 45.8x |
| `stage_3_maturity_pe` | Stage 3 maturity | 20.3x | 24.0x | 28.9x |
| `stage_5_recovery_turnaround_pe` | Stage 5 recovery / turnaround | 27.3x | 34.5x | 42.7x |

最小输入：

```json
{
  "stageMultipleSensitivity": {
    "framework": "stage_2_rapid_growth_pe"
  }
}
```

默认使用主 `scenarios.*.eps`。如果要对比卖方共识、旧表格或另一套 FY+1/FY+2 EPS，可以单独传 EPS：

```json
{
  "stageMultipleSensitivity": {
    "framework": "stage_2_rapid_growth_pe",
    "epsSource": "WallStreetZen FY+1/FY+2 consensus snapshot, checked 2026-05-28",
    "eps": {
      "bear": 13.19,
      "base": 13.15,
      "bull": 14.76
    },
    "labels": {
      "bear": "FY2027 consensus EPS",
      "base": "FY2028 consensus EPS",
      "bull": "FY2028 high EPS"
    }
  }
}
```

使用纪律：

- 表格标题写“Stage Multiple Sensitivity”或“阶段倍数敏感性”，不要写“独立合理价格”。
- 若 EPS 与主模型不同，正文必须披露来源日期、财年混用和可能滞后。
- 对硬件、周期、银行、能源、商品链公司，不得因为它们处于 Stage 2 就自动把该附表作为主估值。
- 若附表显示 `buy` 但主模型不是 `buy`，最终评级以主模型为准；附表只解释情绪上限或外部模型差异。

## Historical Sanity Check

`historicalPriceRanges` 是可选质量检查输入。它只用于比较模型价格带和真实交易区间，不会改变 `fairValue`、`valuationBand` 或 `valuationSignal`。

推荐格式：

```json
{
  "historicalPriceRanges": {
    "3m": {
      "startDate": "2026-02-16",
      "endDate": "2026-05-15",
      "tradingDays": 63,
      "low": 311.25,
      "median": 524.00,
      "high": 803.12,
      "historyMayBeStale": false,
      "notes": "Optional note about major events"
    },
    "6m": {
      "low": 210.00,
      "median": 410.00,
      "high": 803.12
    },
    "12m": {
      "low": 107.67,
      "median": 260.00,
      "high": 803.12,
      "historyMayBeStale": true,
      "notes": "Pre-rerating history may be stale after major AI order or earnings reset"
    }
  }
}
```

字段规则：

- `low` 和 `high` 必填，且 `high >= low`
- `median` 推荐填写，必须在 `low` 与 `high` 之间
- `tradingDays < 60` 时脚本会输出 `short_history`
- `historyMayBeStale = true` 时脚本会输出 `history_may_be_stale`

脚本输出 `historicalSanityCheck`：

- `signal`：`passes_basic_sanity`、`model_disconnected_from_history`、`history_may_be_stale` 或 `short_history`
- `modelRangeOverlap`：`no_overlap`、`partial_overlap`、`covers_full_range`
- `bearPosition / basePosition / bullPosition`：`below_range`、`inside_range`、`above_range`

使用纪律：historical sanity check 只判断模型是否离谱，不得作为独立估值输入。

## Source Discipline

- `price / marketCap`：一个行情源
- `EPS forecast`：一个预期聚合源
- `Revenue forecast`：一个预期聚合源
- `analystTargets`：一个目标价聚合源
- `options`：一个期权统计源
- `news`：优先公司 IR、SEC、主流财经媒体

不要跨源拼同一类字段。

## A/B Model Selection

早期成长和开发阶段股票先分流，再准备 snapshot。

| 公司状态 | 主模型 | 辅助模型 | 示例 |
|---|---|---|---|
| 已规模化收入 + 明确指引 | A：收入三情景 × 独立固定倍数 | B：当前隐含倍数压力测试 | LUNR |
| 早期收入 / 高 EV/Sales / 项目兑现型，但有清晰远期收入共识 | A：独立 forward EV/Sales + 反向估值 | B：当前隐含倍数压力测试 | SMR、ASTS |
| pre-revenue / 纯叙事 | B：固定收入锚或 LTM/implied sensitivity | 仅做补充 | OKLO、XE |

实用阈值：

- forward EV/Sales `< 15x` 且收入指引/共识清晰：A 为主
- forward EV/Sales `15x-30x`：A 为主，B 做压力测试
- forward EV/Sales `> 30x`，但有同一财年低 / 中 / 高收入共识：A 仍为主，并强制加反向估值
- pre-revenue 或收入不可代表未来：B 为主，并明确没有安全边际

对 A：

- 填 `scenarios.bear/base/bull.revenue`
- 填 `multipleFramework`，优先让脚本补齐 `multiple`
- 不使用 `developmentScenarioPolicy`
- 设置 `valuationBasisType = independent_fair_value`
- 对 ASTS / SMR 等早期项目兑现股，优先填 `multipleFramework = early_project_ramp_ev_sales`
- 填 `reverseValuation`，用当前价和目标价反推出收入 / EV/Sales 要求

对 B：

- 若有可用远期收入锚，三个情景使用同一收入，倍数围绕当前隐含 EV/Sales 折溢价
- 若没有前瞻收入共识，使用 `developmentScenarioPolicy = ltm_implied_multiple_sensitivity`
- B 的输出是当前估值敏感性，不是极端牛市上限
- 设置 `valuationBasisType = market_implied_sensitivity`
- 设置 `multipleFramework = development_market_implied_ev_sales`
- 对已有远期收入共识的 ASTS / SMR 等股票，B 只能作为辅助压力测试，不能作为主结论

## Reverse Valuation

`reverseValuation` 用于解释当前价格和目标价隐含的收入 / 倍数要求。它是诊断工具，不改变 `fairValue`、`valuationBand` 或 `valuationSignal`。

推荐用于 `multipleFramework = early_project_ramp_ev_sales`：

```json
{
  "reverseValuation": {
    "targetPrices": [80, 100, 125],
    "multiples": [15, 25, 35],
    "revenueCases": [520400000, 869000000, 1800000000]
  }
}
```

字段：

- `revenueAnchor`：可选；不填时使用 `scenarios.base.revenue`
- `targetPrices`：可选目标价列表
- `multiples`：反推收入时使用的 EV/Sales 倍数列表
- `revenueCases`：可选收入情景列表，用来输出价格矩阵

脚本输出：

- `currentImpliedEvSales`
- `currentPriceRequiredRevenueByMultiple`
- `targetPrices.*.impliedEvSalesAtRevenueAnchor`
- `targetPrices.*.requiredRevenueByMultiple`
- `priceByRevenueAndMultiple`

## Horizon Discipline

默认优先：

- 三个情景都用同一财年

只有在以下情况才允许混用：

- 低位 / 高位预测只在不同财年可得
- 公司正处在极强盈利拐点，近端与远端预期差距极大

混用时必须在 `label` 里写清楚，例如：

- `FY2026 low`
- `FY2027 avg`
- `FY2027 high`

若使用 `EV/Sales`：

- 三个收入情景也优先同一财年
- `netDebt` 与 `dilutedSharesOutstanding` 必须尽量接近同一个报告期
- `netDebt` 定义为 `total debt - cash`
- `netDebt < 0` 代表净现金
- 若公司有双层股权或多股类结构，`dilutedSharesOutstanding` 优先使用**总经济股本**，不要只填单一上市股类的流通股

若使用 `development EV/Sales`：

- `forecastFiscalYear` 必须尽量写清，例如 `FY2028`
- `valuationBasis` 必须说明为什么用远期收入
- 倍数必须说明是 implied future EV/Sales、同行远期倍数、还是阶段假设
- 如果倍数来自 implied future EV/Sales，设置 `valuationBasisType = market_implied_sensitivity`
- 如果倍数来自同行/阶段独立假设，设置 `valuationBasisType = independent_fair_value`
- 远期收入预测不要和近端实际收入混在同一个表里

若没有前瞻收入共识：

- 使用 `developmentScenarioPolicy = ltm_implied_multiple_sensitivity`
- 提供 `ltmRevenue`
- 不提供手工 `scenarios`
- 脚本会用当前隐含 EV/Sales 的 `0.425x / 0.85x / 1.50x` 生成熊 / 基准 / 牛倍数
- 输出含义是当前估值敏感性，不是远期收入预测或独立合理价

## Rating Mapping

脚本会把现价落在哪个价格带里映射成 `priceBand`：

- `deep_value`
- `buy`
- `near_base`
- `hold`
- `expensive`

只有 `valuationBasisType = independent_fair_value` 时，`priceBand` 会同时作为 `valuationBand`。若是 `market_implied_sensitivity`，`valuationBand` 为 `null`，`priceBand` 只会进入 `sensitivityBand`。

## Auto Mode

`valuationMode` 支持：

- `auto`
- `pe`
- `ev_sales`
- `development_ev_sales`

默认建议 `auto`。

自动规则：

- 如果 `developmentStage = true` 且收入、倍数、净债务、稀释股本齐全，优先走 `development_ev_sales`
- 如果基准 EV/Sales 高于 `30x` 且收入、倍数、净债务、稀释股本齐全，切 `development_ev_sales`
- 否则，如果 forward EPS 足够稳定，走 `PE`
- 如果 forward EPS 过低或不稳定，但收入与资产负债表数据可得，切 `EV/Sales`

对近盈利拐点公司，优先准备两套字段，让脚本自己选。
对开发阶段公司，优先准备远期收入、净现金和总经济股本，避免硬套近端收入。
对没有远期收入共识的新股，优先准备 LTM 收入和 `developmentScenarioPolicy`，不要手填三情景倍数。
