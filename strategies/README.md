# 交易策略目录 / Trading Strategies

本目录存放 **自然语言交易策略文件**（YAML 格式）。系统启动时自动加载此目录下所有 `.yaml` 文件。

对用户和文档，我们继续把这些能力称为“策略”；在代码、配置和 API 字段里，它们统一命名为 `skill`，你可以把它理解为“可复用的策略能力包”。

## 如何编写自定义策略（Strategy Skill）

只需创建一个 `.yaml` 文件，用中文（或任意语言）描述你的交易策略即可，**无需编写任何代码**。

### 最简模板

```yaml
name: my_strategy          # 唯一标识（英文，下划线连接）
display_name: 我的策略      # 显示名称（中文）
description: 简短描述策略用途

instructions: |
  你的策略描述...
  用自然语言写出判断标准、入场条件、出场条件等。
  可以引用工具名称（如 get_daily_history、analyze_trend）来指导 AI 使用哪些数据。
```

### 完整模板

```yaml
name: my_strategy
display_name: 我的策略
description: 简短描述策略适用的市场场景

# 策略分类：trend（趋势）、pattern（形态）、reversal（反转）、framework（框架）
category: trend

# 关联的核心交易理念编号（1-7），可选
core_rules: [1, 2]

# 策略需要使用的工具列表，可选
# 可用工具：get_daily_history, analyze_trend, get_realtime_quote,
#           get_sector_rankings, search_stock_news, get_stock_info
required_tools:
  - get_daily_history
  - analyze_trend

# 可选别名（用于 /ask 等自然语言技能选择）
aliases: [我的战法, 我的模型]

# 以下元数据用于驱动默认行为（可选）
# default_active: 是否属于默认激活技能集
# default_router: 是否属于路由 fallback 技能集
# default_priority: 默认展示/排序优先级，数值越小越靠前
# catalog_group: 问股/首页选择器分组，core 或 advanced；未填写默认 advanced
# market_regimes: 该技能优先适配的市场状态标签
default_active: true
default_router: false
default_priority: 100
catalog_group: advanced
market_regimes: [trending_up]

# 策略详细说明（自然语言，支持 Markdown 格式）
instructions: |
  **我的策略名称**

  判断标准：

  1. **条件一**：
     - 使用 `analyze_trend` 检查均线排列。
     - 描述你期望看到的趋势特征...

  2. **条件二**：
     - 描述量能要求...

  评分调整：
  - 满足条件时建议的 sentiment_score 调整
  - 在 `buy_reason` 中注明策略名称
```

### 问股核心与进阶入口

问股与首页默认展示七个核心技能：多策略联合、价格行为学、VCP 突破交易员、日内做 T、期权策略分析、Serenity 投研和价值投资。分组代表用途和证据约束，不代表实盘收益排名。VCP H1/H2 等专项技能保留在“进阶技能”中；EMA200 专项按实际规则标为“5分钟 ORB + EMA200”。

选择器 API 的默认推荐优先从核心组按既有默认/排序元数据选择，目前为多策略联合；没有核心技能时沿用全目录回退。此推荐不修改未显式选择技能时的后台 pipeline 默认规则。

`catalog_group` 只控制选择器展示，不改变技能 ID、自动路由、激活规则、工具或历史会话。已选进阶技能在收起列表后仍显示，历史会话恢复后也保留原选择。API 的 `/api/v1/agent/skills` 及兼容入口 `/strategies` 均返回该追加字段；旧客户端可忽略，新客户端连接未返回该字段的旧服务时继续显示原列表。自定义 YAML 未填写时进入进阶组，`SKILL.md` frontmatter 同时支持 `catalog-group` 和 `catalog_group`；其他分组值会报告加载错误。

价投快捷模板要求核实财报来源、报告期、现金流、负债和估值假设；关键输入缺失时不输出合理买入价或固定账户仓位。综合分析模板保留后端生成的 13 项证据明细与五维得分，缺少账户风险预算时只说明仓位计算方法。股票价格回测与技能真实样本业绩必须分开解释，不能用前者宣称价投或其他技能的实盘胜率。

The selectors show seven core skills and keep specialist skills under an accessible advanced disclosure. `catalog_group` is presentation metadata (`core` or `advanced`, default `advanced`); it does not change IDs, routing, execution or saved selections. Both skill endpoints add this field, while older clients and servers remain compatible. Financial and allocation templates require attributable evidence and distinguish price backtests from skill performance.

### 核心交易理念参考

| 编号 | 理念 |
|------|------|
| 1 | 严进策略：乖离率 < 5% 才考虑入场 |
| 2 | 趋势交易：MA5 > MA10 > MA20 多头排列 |
| 3 | 效率优先：量能确认趋势有效性 |
| 4 | 买点偏好：优先回踩均线支撑 |
| 5 | 风险排查：利空新闻一票否决 |
| 6 | 量价配合：成交量验证价格运动 |
| 7 | 强势趋势股放宽：龙头股可适当放宽标准 |

## 自定义策略目录

除了本目录（内置策略），你还可以通过环境变量指定额外的自定义策略目录：

```env
AGENT_SKILL_DIR=./my_skills
```

系统会同时加载内置策略和自定义策略。如果名称冲突，自定义策略覆盖内置策略。

环境变量名仍然是 `AGENT_SKILL_DIR`，这是内部统一命名后的配置入口；在产品语义上，它依然表示“自定义策略目录”。
