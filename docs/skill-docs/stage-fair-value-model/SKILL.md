---
name: stage-fair-value-model
description: 用「阶段 + 合理价格模型」对单只股票产出快照、阶段判断、熊/基准/牛 EPS×倍数估值、分析师目标价和期权情绪叠加结论。当用户贴出类似“AMKR 阶段 + 合理价格模型”的样稿，问一只股票现在合理价是多少、该给什么阶段、要不要做熊/基准/牛价格带时使用。
---

# 阶段 + 合理价格模型

用本技能回答三个问题：

1. 这只股票现在更像处于哪个增长阶段。
2. 在当前数据下，熊 / 基准 / 牛三个情景的独立合理价格带或市场隐含敏感性带大致在哪。
3. 分析师目标价、新闻催化、期权情绪是否支持这个价格模型。
4. 如果公司有混合属性，同一组 EPS / 收入按不同业务分类口径会得到什么价格带。

这不是 DCF，也不是精确目标价。它默认是**情景 EPS × 估值倍数**模型；当公司仍处于近盈利拐点、forward EPS 过低或不稳定时，自动切到 **EV/Sales**；当公司是 OKLO 这类开发阶段企业时，切到 **development EV/Sales**，再叠加情绪与催化判断。

硬规则：**基准合理价不得直接使用当前股价反推出来的隐含倍数**。如果倍数来自当前 market-implied PE 或 EV/Sales，输出必须叫“市场隐含敏感性 / 压力测试”，不能叫“独立合理价格”。

Fresh-run 硬规则：**默认不要读取 `data/valuation-snapshots/` 里的旧快照作为本次输入**。每次都必须根据当时情况重新联网核验当前价格、最新财报 / 指引、分析师目标价和可得的期权情绪，再新建本次 snapshot。旧 snapshot 只能作为历史参考或复盘材料；只有用户明确要求“复用旧快照 / 回放某日估值 / 对比历史模型”时，才可以把旧 snapshot 当输入。如果无法联网取得关键最新数据，停止并说明缺口，不要用本地旧数据补齐成“当前结论”。

## 与其他技能的关系

- 看懂公司怎么赚钱、护城河和行业结构：`company-teardown`
- 看相对同行贵不贵：`peer-valuation-comparison`
- 看价格位置、支撑阻力、ATR：`stock-level-analysis`
- 本技能只做：**阶段判断 + 合理价格带**
- 默认支持两套估值底座：
  - `PE`：适合盈利已站稳的公司
  - `EV/Sales`：适合 FLNC 这类盈利未站稳、但收入与订单可观察的公司
  - `development EV/Sales`：适合 OKLO 这类远期收入可估、近端收入仍很小的开发阶段公司

## 工作流

### 1. 先建快照，再写结论

- 先读 [references/snapshot-schema.md](references/snapshot-schema.md)
- 不要读取 `data/valuation-snapshots/` 里的旧 snapshot 作为本次输入；先联网核验当前价格、最新财报 / 指引、分析师目标价和期权数据
- 用 dated source 填完新 snapshot；每个关键字段在 `sources` 里写清 URL 和 checked 时间
- 同一类数据只信一个源：股价一套、EPS 一套、分析师目标价一套、期权一套
- 能用同一财年就不要混 `FY+1` 和 `FY+2`
- 如果必须混，正文里必须明确写出每个情景对应的财年
- 对早期成长 / 开发阶段公司，先按下方 A/B 分流规则决定主模型，不要直接套模板

### 2. 用脚本算，不要手算

把新快照保存成 JSON 后运行。若只是本次分析的中间文件，优先保存到 `/tmp/stage-fair-value-model/`；若用户要求长期保存到仓库，文件名必须包含日期和分钟级时间戳，格式为 `symbol-YYYY-MM-DD-HHMM.json`，例如 `te-2026-06-08-1730.json`，避免当天旧快照被误认为最新输入。

```bash
python3 skills/stage-fair-value-model/scripts/calculate_snapshot.py /path/to/snapshot.json
```

脚本负责：

- 在 `auto` 模式下自动选择 `pe`、`ev_sales` 或 `development_ev_sales`
- 校验情景顺序（bear <= base <= bull）
- 计算 `fair value = EPS × multiple`
- 或 `fair value = (Revenue × EV/Sales - Net Debt) ÷ Diluted Shares`
- 对开发阶段公司标记 `developmentStage`、远期收入年份和高倍数风险
- 计算相对现价的上行/下行空间
- 输出估值带标签
- 区分 `valuationBasisType = independent_fair_value` 与 `market_implied_sensitivity`
- 可选输出 `stageMultipleSensitivity`，用于对比外部 Stage PE 倍数模型
- 可选输出 `valuationFamilies`，用于生成“分类口径 × 熊/基准/牛”估值表
- 给分析师目标价和期权比率做基础 overlay

### 2.0 估值口径分离

每次 snapshot 必须尽量标明 `valuationBasisType`：

- `independent_fair_value`：倍数来自历史区间、同行区间、阶段规则或基本面质量判断。这个口径才允许输出“合理价格 / 估值评级”。
- `market_implied_sensitivity`：倍数来自当前股价反推的 forward PE、EV/Sales 或 LTM implied multiple。这个口径只能输出“市场隐含预期 / 压力测试”，不能给独立买入信号。
- `hybrid`：混合了独立倍数与市场隐含倍数。正文必须拆开说明，不能把混合结果包装成单一合理价。

如果没有填写 `valuationBasisType`，脚本会根据 `developmentScenarioPolicy` 或 `valuationBasis` 里的 `current implied / market implied / 隐含倍数` 自动推断。自动推断只能防错，不能替代正文披露。

| 口径 | 倍数来源 | 能回答的问题 | 不能回答的问题 |
|---|---|---|---|
| 独立合理价 | 历史 / 同行 / 阶段规则 | 现价相对独立锚偏贵还是偏便宜 | 市场短期是否还会继续疯狂 |
| 市场隐含敏感性 | 当前股价反推 | 当前价格隐含什么预期，倍数压缩/扩张会怎样 | 这只股票本身是否便宜 |

若使用当前隐含倍数：

- 不得写“基准合理价证明现价合理”
- 应写“若市场继续维持当前隐含倍数，价格约为...”
- 最终评级写成“无法给独立估值信号 / 仅作市场预期压力测试”

### 2.0.1 模型置信度与质量警报

脚本会输出 `modelConfidence`、`qualityWarnings`、`modelUse` 和 `guardrailedValuationSignal`。写结论时必须先看这四个字段，再引用 `valuationSignal`。

| 字段 | 含义 | 写作纪律 |
|---|---|---|
| `modelConfidence = high` | PE / EV/Sales 口径、卖方目标价、历史区间和风险旗标没有明显冲突 | 可以引用 `valuationSignal`，但仍需披露假设 |
| `modelConfidence = medium` | 有模型风险，但还可作方向性估值 | 可以写“方向性偏便宜 / 偏贵”，不能写精确目标价 |
| `modelConfidence = low` | 模型与目标价/历史/业务阶段冲突，或风险旗标过强 | 不得直接写“买入 / 便宜 / 昂贵”，只能写诊断、反向估值或观察清单 |
| `guardrailedValuationSignal` | 经置信度和口径过滤后的可引用信号 | 最终估值评级优先引用它，而不是裸 `valuationSignal` |

推荐在 snapshot 中填写 `riskFlags`，让脚本把已知风险纳入置信度：

```json
"riskFlags": [
  "cyclical_peak_eps_risk",
  "margin_path_unproven",
  "early_project_execution_risk",
  "current_revenue_cannot_explain_market_cap",
  "dilution_or_financing_risk"
]
```

常用风险旗标：

| riskFlag | 典型场景 | 影响 |
|---|---|---|
| `cyclical_peak_eps_risk` | 存储、商品、航运、强周期盈利可能接近上行周期峰值 | 降低 PE 模型置信度，要求用中周期 EPS 复核 |
| `current_revenue_cannot_explain_market_cap` | KEEL / RKLB 等当前收入解释不了市值 | 强制弱化独立合理价，转向反向估值 |
| `reverse_valuation_primary` | 主要结论来自当前价反推收入或倍数 | 不给独立估值信号 |
| `margin_path_unproven` | EV/Sales 依赖未来利润率兑现 | 要补毛利率 / FCF bridge |
| `early_project_execution_risk` | ASTS / SMR / LUNR 等项目兑现型公司 | 收入情景仍可用，但降为方向性模型 |
| `dilution_or_financing_risk` | 未来建设、研发或产能扩张需要融资 | 牛市价格不得忽略潜在稀释 |

低置信模型的正确输出方式：

- 先写 `modelUse` 和关键 `qualityWarnings`
- 再写“当前市场需要相信什么”
- 最后写“哪些数据能让模型升级 / 降级”
- 不要把低置信区间包装成精确合理价

### 2.1 固定倍数框架

能匹配框架时，必须填写 `multipleFramework`，并优先让脚本自动补齐 `multiple`。不要每次临场拍倍数。

| multipleFramework | 适用对象 | 模式 | 熊 | 基准 | 牛 |
|---|---|---|---:|---:|---:|
| `traditional_memory_cycle_pe` | 传统 memory 周期 | PE | 5x | 7x | 9x |
| `ai_memory_upcycle_pe` | MU / SNDK / WDC 等 AI memory 上行周期 | PE | 8x | 10x | 12x |
| `cyclical_semiconductor_pe` | 一般强周期半导体 | PE | 6x | 8x | 10x |
| `mature_semiconductor_pe` | 成熟大盘半导体 | PE | 18x | 25x | 32x |
| `premium_ai_platform_pe` | NVDA 等高质量 AI 平台，但需要避免无限外推高倍数 | PE | 20x | 25x | 30x |
| `mega_cap_ai_cloud_pe` | MSFT 等大盘 AI/cloud 复利平台 | PE | 20x | 28x | 36x |
| `mature_quality_software_pe` | GOOGL / 大盘软件服务等成熟质量增长 | PE | 18x | 23x | 28x |
| `consumer_platform_pe` | AAPL 等消费硬件 + 服务生态平台 | PE | 20x | 25x | 30x |
| `mega_cap_ad_platform_pe` | META / 广告平台型大盘成长 | PE | 18x | 23x | 28x |
| `premium_semiconductor_ip_pe` | ARM 这类高质量半导体 IP 平台 | PE | 45x | 65x | 85x |
| `high_growth_profitable_pe` | 已盈利高增长平台 | PE | 35x | 50x | 70x |
| `financial_hybrid_pe` | 支付 / fintech 里金融服务占比高、信用或监管风险更重 | PE | 12x | 18x | 24x |
| `payment_fintech_pe` | PayPay / PayPal / Block 这类支付网络 + 金融交叉销售平台 | PE | 18x | 25x | 32x |
| `premium_fintech_platform_pe` | 被当成高成长超级金融 app / fintech 平台重估 | PE | 25x | 35x | 45x |
| `cyclical_midcycle_pe` | 盈利强周期且需用中周期 EPS 复核 | PE | 6x | 8x | 10x |
| `networking_platform_pe` | ANET 等 AI 网络平台 | PE | 25x | 35x | 45x |
| `optical_component_cycle_pe` | CIEN / COHR / LITE 等光模块或网络部件周期 | PE | 18x | 25x | 32x |
| `ai_power_profit_pe` | VST / CEG 等 AI 电力但盈利已规模化 | PE | 16x | 22x | 28x |
| `ai_power_growth_pe` | GEV / BE 等 AI 电力增长溢价更高的公司 | PE | 25x | 35x | 45x |
| `defensive_consumer_pe` | KO / PEP / PG 等防御消费 | PE | 18x | 22x | 26x |
| `defensive_healthcare_pe` | JNJ 等防御医疗 | PE | 14x | 17x | 20x |
| `defensive_restaurant_pe` | MCD 等防御餐饮品牌 | PE | 20x | 24x | 28x |
| `regulated_utility_pe` | NEE 等公用事业 / 受监管电力 | PE | 18x | 22x | 26x |
| `storage_integrator_ev_sales` | 低毛利储能硬件 / 项目集成商 | EV/Sales | 0.5x | 0.8x | 1.2x |
| `scaled_growth_ev_sales` | 收入规模化但利润不稳的成长股 | EV/Sales | 3x | 5x | 8x |
| `energy_storage_scaled_ev_sales` | FLNC 等储能集成 / backlog 型公司 | EV/Sales | 0.8x | 1.2x | 1.8x |
| `ai_data_center_power_storage_ev_sales` | 被 AI 数据中心电力 / 储能基础设施叙事重估 | EV/Sales | 1.2x | 2.0x | 3.0x |
| `ai_chip_hardware_ev_sales` | AI 芯片 / 硬件供应商，客户集中和硬件周期打折 | EV/Sales | 10x | 15x | 25x |
| `ai_infra_growth_ev_sales` | AI inference infrastructure，硬件 + 系统 + 软件一体化 | EV/Sales | 25x | 40x | 60x |
| `post_ipo_scarcity_premium_ev_sales` | 刚 IPO 稀缺 AI 标的 / 情绪溢价 | EV/Sales | 60x | 85x | 120x |
| `current_implied_base_revenue_sensitivity` | 当前价按基准收入反推的 EV/Sales 压力测试 | EV/Sales | implied ×0.7 | implied ×1.0 | implied ×1.4 |
| `space_defense_scaled_ev_sales` | LUNR 等收入规模化的航天/国防项目型公司 | EV/Sales | 5x | 8x | 12x |
| `early_project_ramp_ev_sales` | ASTS / SMR 等有远期收入共识但仍处项目兑现期的公司 | EV/Sales | 15x | 25x | 35x |
| `ai_power_scaled_ev_sales` | BE 等 AI 数据中心电力放量公司 | EV/Sales | 12x | 20x | 30x |
| `development_market_implied_ev_sales` | OKLO / XE / SMR 等开发阶段项目兑现型公司 | development EV/Sales | implied ×0.425 | implied ×0.85 | implied ×1.50 |

脚本规则：

- 如果 `multipleFramework` 已填且某个情景缺 `multiple`，脚本按框架自动填入。
- 如果手填 `multiple` 与框架值不一致，脚本保留手填值，但输出 warning。
- `development_market_implied_ev_sales` 不是直接填 `0.85x`；脚本会先算当前 implied EV/Sales，再乘以框架因子。
- 无法匹配框架时，使用 `multipleFramework = custom_framework`，但正文必须说明倍数来源，并降低复现置信度。

### 2.1.1 分类口径估值表

每次输出都要给一张“分类口径 × 熊 / 基准 / 牛”的表。它回答：**如果市场把这家公司归到不同业务族，价格带会怎么变。**

规则：

- 主模型仍只选一个；不要把多分类表做成加权平均合理价。
- 混合公司至少给三行：保守风险分类、主分类、情绪 / 稀缺性上限。
- 纯公司也至少给两行：主分类、保守或上限分类。
- `current_implied_*` 只能叫“市场隐含压力测试”，不能叫独立合理价。
- `分类口径` 列必须使用英文：优先使用 `multipleFramework` / `classification` 的英文 snake_case 标识，例如 `payment_fintech_pe`、`ai_chip_hardware_ev_sales`、`scarce_ai_optical_infrastructure_bottleneck`。不要在这一列写中文行业描述；中文解释放在 `适用逻辑` 或 `解释`。
- 表格必须按固定列顺序输出：`分类口径`、`适用逻辑`、`PE 假设` / `EV/Sales 假设` / `development EV/Sales 假设`、`熊`、`基准`、`牛`、`解释`。
- 倍数假设列必须显式写出三档倍数，例如 `12 / 18 / 24x`、`0.8 / 1.2 / 1.8x`、`35 / 50 / 70x`。不要只在正文里解释倍数，也不要省略。
- `熊`、`基准`、`牛` 三列默认写每股价格；若用户追问 EV 或某行是高倍数叙事口径，可在该行解释或正文补充对应 EV，但不要改变主表列顺序。
- 表格示例：

| 分类口径 | 适用逻辑 | PE 假设 | 熊 | 基准 | 牛 | 解释 |
|---|---|---:|---:|---:|---:|---|
| `financial_hybrid_pe` | 支付 + 金融服务，利润质量 / 监管 / 信用风险打折 | 12 / 18 / 24x | $6.24 | $10.26 | $17.28 | 保守模型 |
| `payment_fintech_pe` | 支付网络 + 用户平台，take rate + 金融交叉销售 | 18 / 25 / 32x | $9.36 | $14.25 | $23.04 | 中性主模型 |
| `premium_fintech_platform_pe` | 被市场当高成长 fintech 平台重估 | 25 / 35 / 45x | $13.00 | $19.95 | $32.40 | 牛市 / 情绪上限 |

snapshot 里优先填写 `valuationFamilies`，让脚本自动计算：

```json
"valuationFamilies": [
  {
    "classification": "financial_hybrid_pe",
    "framework": "financial_hybrid_pe",
    "role": "conservative",
    "applicability": "支付 + 金融服务，利润质量和监管风险打折",
    "explanation": "保守下沿"
  },
  {
    "classification": "payment_fintech_pe",
    "framework": "payment_fintech_pe",
    "role": "main_model",
    "applicability": "支付网络 + 用户平台 + 金融交叉销售",
    "explanation": "主模型"
  },
  {
    "classification": "premium_fintech_platform_pe",
    "framework": "premium_fintech_platform_pe",
    "role": "sentiment_upper",
    "applicability": "被市场当高成长 fintech 平台重估",
    "explanation": "牛市 / 情绪上限",
    "basisType": "market_sentiment_sensitivity"
  }
]
```

脚本会使用主 `scenarios` 的 EPS 或收入，只替换每个分类口径的倍数。若某个分类没有固定框架，可在该行显式填：

```json
"multiples": { "bear": 20, "base": 28, "bull": 36 }
```

### 2.1.2 Stage Multiple Sensitivity 附表

当用户拿外部“Stage 2 = 29.2/36.5/45.8x”这类模型来对比，或要求“看一下如果市场按阶段倍数重估能到哪里”，可以在 snapshot 里加 `stageMultipleSensitivity`。

它是**附表**，不是主估值：

- 不改变 `valuationBand`、`valuationSignal` 和最终估值评级。
- 标题必须写“Stage Multiple Sensitivity / 阶段倍数敏感性”，不要写“独立合理价格”。
- 它解释市场情绪上限、外部模型差异或“如果被当成高成长平台定价”的价格带。
- 对 DELL、MU、MPC、VALE、IREN 这类硬件 / 周期 / 商品 / 挖矿相关公司，不得因为处于 Stage 2 就把 Stage 2 高倍数作为主模型。

支持的 `framework`：

| framework | 适用说明 | 熊 | 基准 | 牛 |
|---|---|---:|---:|---:|
| `stage_2_rapid_growth_pe` | 外部 Stage 2 快速增长倍数 | 29.2x | 36.5x | 45.8x |
| `stage_3_maturity_pe` | 外部 Stage 3 成熟复利倍数 | 20.3x | 24.0x | 28.9x |
| `stage_5_recovery_turnaround_pe` | 外部 Stage 5 修复 / turnaround 倍数 | 27.3x | 34.5x | 42.7x |

最小输入：

```json
"stageMultipleSensitivity": {
  "framework": "stage_2_rapid_growth_pe"
}
```

默认使用主模型的 `scenarios.*.eps`。如果要复现外部模型，可以单独传 EPS：

```json
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
```

如果附表 EPS 与主模型不同，正文必须写清：

- 来源日期；
- FY+1 / FY+2 是否混用；
- 是否可能落后于最新公司指引；
- 为什么它只是“共识 / 情绪敏感性”，不是主合理价。

### 2.2 历史区间 sanity check

能拿到历史价格时，建议提供 `historicalPriceRanges`，用于校验模型区间是否脱离真实交易区间。它是质量检查，不是估值输入。

推荐至少填：

- `3m`：最近 3 个月低点 / 中位数 / 高点
- `6m`：最近 6 个月低点 / 中位数 / 高点
- `12m`：最近 12 个月低点 / 中位数 / 高点

示例：

```json
"historicalPriceRanges": {
  "3m": {
    "startDate": "2026-02-16",
    "endDate": "2026-05-15",
    "tradingDays": 63,
    "low": 311.25,
    "median": 524.00,
    "high": 803.12,
    "historyMayBeStale": false
  }
}
```

脚本会输出：

- `modelRangeOverlap`：模型熊/牛区间与历史区间是否重叠
- `bearPosition / basePosition / bullPosition`：各价格落在历史区间内、上方还是下方
- `signal`：`passes_basic_sanity`、`model_disconnected_from_history`、`history_may_be_stale` 或 `short_history`

使用纪律：

- 不得因为历史区间而自动调整合理价。
- 若公司刚发生重大财报、订单、监管、并购或稀释事件，设置 `historyMayBeStale = true`。
- 交易历史少于 60 个交易日时，只能写弱判断。
- 新股 / SPAC / 重大重估股不要把 12M 区间当成强锚。

### 2.3 早期成长 / 开发阶段 A/B 分流

对收入未完全成熟的股票，先分流：

| 公司状态 | 主模型 | 辅助模型 | 典型样本 |
|---|---|---|---|
| 已规模化收入 + 明确指引 | A：收入三情景 × 独立固定倍数 | B：当前隐含倍数折价压力测试 | LUNR |
| 早期收入 / 高 EV/Sales / 项目兑现型，但有清晰远期收入共识 | A：独立 forward EV/Sales + 反向估值 | B：当前隐含倍数压力测试 | ASTS、SMR |
| pre-revenue / 纯叙事 | B：固定收入锚或 LTM/implied sensitivity | 仅作为补充，不给安全边际 | OKLO、XE |

实用阈值：

- forward EV/Sales `< 15x`，且收入指引/共识清晰：A 为主
- forward EV/Sales `15x-30x`：A 为主，B 做压力测试
- forward EV/Sales `> 30x`，但有同一财年低 / 中 / 高收入共识：A 仍为主，并强制加反向估值
- pre-revenue 或收入不可代表未来：B 为主，并明确没有安全边际

A 的含义：

- 收入做熊 / 基准 / 牛三情景
- 倍数用相对固定的独立阶段假设
- 适合收入规模已经能解释估值的一部分
- `valuationBasisType = independent_fair_value`
- 对 ASTS / SMR 这类早期项目兑现股，优先用 `multipleFramework = early_project_ramp_ev_sales`
- 同时填写 `reverseValuation`，说明当前价和目标价分别要求多少收入或 EV/Sales

B 的含义：

- 收入锚固定
- 倍数围绕当前市场隐含 EV/Sales 做折价 / 溢价敏感性
- 适合项目兑现型、叙事型、高倍数股票
- B 不是完整牛市上限模型；极端牛市可能高于 B 的牛市档
- `valuationBasisType = market_implied_sensitivity`
- B 的输出不是独立合理价值，不得据此写“买入 / 便宜”
- B 不得作为 ASTS 这类已有远期收入共识股票的主结论；它只回答“当前叙事降温 / 升温会怎样”

### 2.3.1 反向估值

对 `early_project_ramp_ev_sales` 必须提供 `reverseValuation`：

```json
"reverseValuation": {
  "targetPrices": [80, 100, 125],
  "multiples": [15, 25, 35],
  "revenueCases": [520400000, 869000000, 1800000000]
}
```

脚本会输出：

- 当前价隐含的 EV/Sales
- 当前价在 15x / 25x / 35x 下需要多少收入
- 目标价在 15x / 25x / 35x 下需要多少收入
- 收入低 / 中 / 高分别配不同倍数时的价格矩阵

反向估值只解释“市场在押什么”，不能替代独立合理价。

### 2.4 自动切换规则

默认 `valuationMode = auto`，脚本按以下规则判定：

- 若 `developmentStage = true`，且三情景收入、倍数、净债务、稀释股本齐全，优先切到 `development_ev_sales`，即使也填了 EPS
- 若基准 EV/Sales `>= 30x`，且三情景收入、倍数、净债务、稀释股本齐全，切到 `development_ev_sales`
- 若三情景都有 `eps` 与 `multiple`，且：
  - `bear EPS >= 0.25`
  - `base EPS >= 0.50`
  - 三情景 EPS 都为正
  - 则优先用 `PE`
- 否则，只要三情景都有 `revenue` 与 `multiple`，且提供了：
  - `netDebt`
  - `dilutedSharesOutstanding`
  - 则切到 `EV/Sales`
- 收入预测年份明显远于当前年度，且公司仍处第一阶段时，也应人工设置 `developmentStage = true`

如果用户明确指定：

- `valuationMode = pe`
- `valuationMode = ev_sales`
- `valuationMode = development_ev_sales`

则脚本尊重人工指定，但会在输出里保留 warning。

### 3. 再做阶段判断

阶段不是抓来的字段，是推断。默认用这套四阶段：

- `第一阶段：早期 / 叙事期`
  - EPS 为负或极不稳定
  - 市场主要按收入、份额、用户或主题定价
- `第二阶段：快速增长 / 重估期`
  - EPS 已转正或正快速抬升
  - 收入 / 盈利增速高，市场愿意给高 forward multiple
- `第三阶段：成熟增长 / 质量期`
  - EPS 稳定为正
  - 增速回落但质量更高，估值更多锚定 earnings / FCF
- `第四阶段：成熟 / 周期 / 价值期`
  - 增速较低或强周期
  - 估值主要锚定中周期盈利、现金回报、资产负债表

如果证据跨阶段，写成：

- `第二 / 第三阶段交界`
- `第二阶段，但周期性很强`

不要硬判。

### 4. 倍数是模型输入，不是事实

倍数必须说明依据，允许的来源只有三类：

- 同行业历史 / 当前合理区间
- 阶段映射后的主观折价或溢价
- 公司自身历史区间或周期中位数

当前 forward PE 附近只能用于 `market_implied_sensitivity`，不能用于 `independent_fair_value` 的基准倍数。

若使用 `EV/Sales`，则把上面这三类替换成：

- 同行业历史 / 当前合理区间
- 阶段映射后的主观折价或溢价
- 公司自身历史区间或周期中位数

当前 forward EV/Sales 附近只能用于 `market_implied_sensitivity`，不能用于 `independent_fair_value` 的基准倍数。

若使用 `development_ev_sales`，必须额外说明：

- 使用的是哪一年收入预测，例如 `FY2028`
- 基准倍数是否来自独立假设，还是贴近当前 implied future EV/Sales
- 这是项目兑现概率估值，不是传统便宜倍数
- 净现金只提供底线缓冲，不代表主营价值已被验证

若没有公开前瞻收入共识，使用固定策略：

- `developmentScenarioPolicy = ltm_implied_multiple_sensitivity`
- 三情景收入全部用 `ltmRevenue`
- 当前隐含 EV/Sales = `(currentPrice × dilutedSharesOutstanding + netDebt) ÷ ltmRevenue`
- 熊市倍数 = 当前隐含 EV/Sales × `0.425`
- 基准倍数 = 当前隐含 EV/Sales × `0.85`
- 牛市倍数 = 当前隐含 EV/Sales × `1.50`

不要在这个场景下手工换成 70/105/140 或 45/90/160；要让脚本派生，保证同一股票可复现。

不要把倍数写成“抓到的事实”。它是模型假设。

不要把“当前股价反推出来的倍数”再乘回同一个 EPS / 收入，并称为“合理价格”。这只是在复原当前市场价格。

## 缺失数据纪律

- 缺 `currentPrice`：停止
- 在 `PE` 路径下缺 `eps` 或 `multiple`：停止
- 在 `EV/Sales` 或 `development_ev_sales` 路径下缺 `revenue`、`multiple`、`netDebt` 或 `dilutedSharesOutstanding`：停止
- 在 `development_ev_sales` 路径下缺 `forecastFiscalYear`：继续，但正文必须说明远期年份口径缺失
- 在开发阶段且缺公开前瞻收入共识时：优先提供 `ltmRevenue` 与 `developmentScenarioPolicy`，让脚本自动生成情景
- 缺 `analystTargets`：继续，但第 6 节明确写“卖方 overlay 缺失”
- 缺 `options`：继续，但第 6 节明确写“期权 overlay 缺失”
- 缺新闻 / 财报更新：继续，但降低阶段判断置信度

## 输出格式

默认用这个结构，除非用户要求更短：

```markdown
## SYMBOL（Company）——阶段 + 合理价格模型（截至 YYYY-MM-DD）

### 0）快照
- 股价：$...
- 数据核验时间：YYYY-MM-DD HH:MM TZ
- 本次 snapshot：新建 / 临时 / 已保存到 `symbol-YYYY-MM-DD-HHMM.json`
- 市值：...
- 增长阶段：...
- 核心主题：...
- 估值口径：`independent_fair_value` 或 `market_implied_sensitivity`
- 模型置信度：`high / medium / low`
- 可引用信号：`guardrailedValuationSignal`
- 关键警报：列出 `qualityWarnings` 的 high / medium 项

### 1）增长阶段
- 为什么是这个阶段
- 若有争议，写清交界点

### 2）估值输入阶梯
- 若 `PE`：
  - 熊市 EPS：...
  - 基准 EPS：...
  - 牛市 EPS：...
- 若 `EV/Sales` / `development EV/Sales`：
  - 熊市收入：...
  - 基准收入：...
  - 牛市收入：...
- 逐项标明财年与来源口径

### 3）估值倍数输入
- 若 `PE`：写 `PE`
- 若 `EV/Sales`：写 `EV/Sales`
- 若 `development EV/Sales`：写 `development EV/Sales`
- `multipleFramework`：...
- 熊市倍数：...
- 基准倍数：...
- 牛市倍数：...
- 一句话解释为什么给这个倍数

### 4）合理价格 / 市场隐含敏感性
| 情景 | EPS | 倍数 | 合理价值 | 相对现价 |
|---|---:|---:|---:|---:|
| 熊市 | ... | ... | ... | ... |
| 基准 | ... | ... | ... | ... |
| 牛市 | ... | ... | ... | ... |

- 若 `EV/Sales` 或 `development EV/Sales`，正文要加一行 bridge：
  - `EV = Revenue × EV/Sales`
  - `Equity Value = EV - Net Debt`
  - `Fair Price = Equity Value ÷ Diluted Shares`
- 若 `valuationBasisType = market_implied_sensitivity`，表名必须写“市场隐含敏感性”，不要写“合理价格”

### 4.5）分类口径估值表
| 分类口径 | 适用逻辑 | PE 假设 / EV/Sales 假设 | 熊 | 基准 | 牛 | 解释 |
|---|---|---:|---:|---:|---:|---|
| `english_framework_or_classification` | ... | 12 / 18 / 24x | $... | $... | $... | 保守 / 主模型 / 上限 |

- `分类口径` 必须是英文 snake_case，不要写中文分类名。
- 第三列表头按主模型类型改名：PE 模型写 `PE 假设`，EV/Sales 模型写 `EV/Sales 假设`，development EV/Sales 模型写 `development EV/Sales 假设`。
- 第三列必须写三档倍数：`熊 / 基准 / 牛`，例如 `12 / 18 / 24x` 或 `35 / 50 / 70x`。
- `熊`、`基准`、`牛` 三列写每股价格，不写 EV；如果需要解释某个高倍数口径对应多少 EV，在 `解释` 或正文补一句。
- 明确标出哪一行是主模型。
- 不输出加权平均价；如果当前价格只能由上限分类解释，直接写清。
- `market_sentiment_sensitivity` 和 `market_implied_sensitivity` 行不得改变主估值评级。

### 5）反向估值（若有）
- 当前价隐含的 EV/Sales
- 当前价在关键倍数下需要的收入
- 目标价在关键倍数下需要的收入
- 收入 × 倍数价格矩阵
- 明确写：反向估值只解释市场在押什么，不是合理价

### 5.5）Stage Multiple Sensitivity（若有）
- 写明 framework、EPS 来源和倍数
- 表格展示熊 / 基准 / 牛价格和相对现价
- 明确写：这是阶段倍数敏感性，不改变主估值评级
- 若它与主模型结论不同，解释差异来自 EPS 来源、财年选择还是倍数假设

### 6）分析师叠加判断
- 平均 / 最高 / 最低目标价
- 当前价相对卖方均值是溢价还是折价

### 7）历史区间 sanity check
- 3M / 6M / 12M 价格区间
- 模型熊 / 基准 / 牛价是否脱离真实交易区间
- 若历史可能失效，明确写 `historyMayBeStale`

### 8）新闻 / 政策 / 情绪 / 资金流
- 最近财报或产业催化
- Put/Call 成交量与 OI 比
- 情绪结论

### 9）最终三重评级
- 阶段基本面评级：...
- 合理价格 / 估值评级：优先引用 `guardrailedValuationSignal`；若为 `low_confidence_no_standalone_signal`，只能写“低置信诊断”
- 新闻 / 情绪评级：...
```

## 判断口径

估值结论默认按脚本输出的 band：

- `deep_value`：现价低于熊市合理价值
- `buy`：现价高于熊市、但低于基准
- `near_base`：现价在基准合理价值上下 5% 内
- `hold`：现价高于基准、但未超过牛市
- `expensive`：现价高于牛市

但只有 `valuationBasisType = independent_fair_value` 时，`valuationBand` 才能作为估值信号。

即使 `valuationBasisType = independent_fair_value`，如果 `modelConfidence = low`，最终也不得直接引用裸 `valuationSignal`。此时写：

- `guardrailedValuationSignal = low_confidence_no_standalone_signal`
- 结论为“模型低置信，只能作为诊断 / 观察清单”
- 用 `qualityWarnings` 解释是周期 EPS、目标价偏离、历史脱节、项目兑现还是融资风险导致降级

若 `valuationBasisType = market_implied_sensitivity`：

- 脚本输出 `valuationSignal = not_independent`
- `valuationBand = null`
- `sensitivityBand` 只表示现价落在压力测试区间的什么位置
- 最终不能写“买入 / 便宜”，只能写“当前市场隐含预期偏高 / 偏低 / 接近基准压力测试”

若 `valuationBasisType = hybrid`：

- 脚本输出 `valuationSignal = mixed_basis`
- 正文必须拆成独立合理价与市场隐含压力测试两段
- 不要把 hybrid 输出当作最终价格目标

分析师 overlay 默认按平均目标价与现价关系：

- 高于现价 10% 以上：`bullish`
- 在现价 ±10%：`mixed`
- 低于现价 10% 以上：`cautious`

期权 overlay 默认按 Put/Call：

- 成交量比 `< 0.7`：短线偏多
- 成交量比 `0.7 - 1.2`：中性
- 成交量比 `> 1.2`：短线偏空
- OI 比 `< 0.85`：持仓偏多
- OI 比 `0.85 - 1.15`：持仓中性
- OI 比 `> 1.15`：持仓偏防守

## 什么时候不用 PE

出现以下任一情形，优先切 `EV/Sales`：

- 未来 12-18 个月 `EPS` 仍接近 0
- GAAP / Non-GAAP EPS 摆动极大
- 公司核心叙事仍是 backlog、订单、收入放量，而不是稳定盈利
- 用 `PE` 算出来熊市价格接近 0，导致模型失真

## 什么时候用 development EV/Sales

出现以下任一情形，优先使用 `development_ev_sales`：

- 近端收入很小，但 2-5 年收入预测可得
- 公司仍处第一阶段，估值主要来自监管、工程、产能或订单里程碑
- 基准 EV/Sales 高于 `30x`
- 分析师目标价主要隐含项目兑现概率，而不是近端 EPS

这个模式的输出要比普通 `EV/Sales` 更克制：如果使用市场隐含倍数，脚本不会给独立 `valuationBand`，只能把结果写成“市场隐含压力测试；没有独立安全边际结论”。

若开发阶段公司没有卖方远期收入预测，必须走 `ltm_implied_multiple_sensitivity`。这会把差异限定在当前市场隐含倍数的折扣/溢价带上，输出的是“当前估值敏感性”，不是“远期收入爬坡估值”。

## 常见错误

- 把 `stage` 当成现成字段抓下来
- EPS 用 `GAAP`，倍数却拿 `Non-GAAP forward PE`
- 熊 / 基准 / 牛混了不同来源、不同日期、不同财年却不披露
- 倍数没解释来源，直接拍脑袋
- forward EPS 还没站稳，却硬套 `PE`
- pre-revenue / development-stage 公司硬套普通 `EV/Sales`，没有标明远期收入和高倍数风险
- 没有前瞻收入共识时，手工选择三组收入或三组倍数，导致不同 agent 复现不了
- 用当前股价反推倍数，再把它写成“基准合理价”
- 对 `market_implied_sensitivity` 输出 `buy / near_base` 结论
- 忽略 `modelConfidence = low`，仍然直接输出“买入 / 便宜 / 昂贵”
- 存储、商品、硬件周期股只用下一年高峰 EPS，不标 `cyclical_peak_eps_risk`
- 项目兑现型公司只给 EV/Sales 表，不做反向估值和收入兑现条件
- 把历史价格区间当成合理价输入，而不是 sanity check
- 公司刚财报重估后，没有设置 `historyMayBeStale`
- 现价、市值、EPS、目标价、期权分别来自五个口径不同的站
- 用文风掩盖数据缺失

## 可选的本地记录

如果用户要求保存结果，用本地记录模型：

- `title`：例如 `SNDK 阶段 + 合理价格模型`
- `content`：完整分析正文
- `source`：`ai-analysis`
- `status`：`pending`
- `tags`：代码、`阶段模型`、`合理价格`、关键主题
