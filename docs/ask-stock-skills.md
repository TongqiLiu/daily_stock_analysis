# 问股技能：选择、证据与回测

问股默认展示七个常用入口。分类是用途与实现边界，不是收益排名；没有足够可归因的技能样本时，不宣称技能胜率或自动调整权重。

| 常用入口 | 回答的问题 | 必须注意的边界 |
| --- | --- | --- |
| ⚡ 多策略联合 | 方向、价格结构及持仓/空仓动作 | 13 行证据经五维合并；相关指标不重复充当独立投票，缺数据不补中性分 |
| 📊 价格行为学 | 趋势/区间、重要低点、结构失效和保护参考 | 二次入场只作待确认，VWAP 依赖真实日内数据 |
| 🚀 VCP突破交易员 | 日线收缩、提前买点与突破候选 | 使用已完成日线及有效 OHLCV；未收盘或缺成交量不确认信号 |
| 🔄 日内做T | 3 分钟高位失败、低位修复及价差空间 | 依赖日内行情；T 仓只针对可卖持仓，不能直接当账户配置 |
| 🎯 期权策略分析 | 正股方向与实际期权报价是否支持组合 | 真实链、报价和组合分析权限必须可用；日线股票回测不能验证期权收益 |
| 🔎 Serenity投研 | 事件怎样传导至经营、估值与投资假设 | 摘要不等于研报全文；情景概率和多个研究镜头不等于统计验证 |
| 💎 价值投资 | 业务质量、价格及账户适配性 | 财务期间与来源必须核验；自动 DCF 是敏感性演示，详见[价投说明](value-investing-skill.md) |

进阶技能可展开选择，旧技能 ID、已保存会话和自定义技能仍然兼容。VCP H1/H2 保留为进阶变体，不与突破交易员重复计票；`ema5_200_setup` 显示为“5m ORB＋EMA200”，避免误认为通用日线回踩。`catalog_group: core` 控制常用展示，未声明时进入进阶区，不影响 `default_active` 的内部分析语义。

## 统一的报告边界

- 数据源、截至时间、财报期间分别列出，抓取时间不当作财报发布日期。用户提供的数据须标明来源，不能默认为已由券商核验。
- 数据缺失或口径冲突会降低覆盖率与结论强度；没有财务历史时不生成“连续五年稳定”等判断。情绪源失败返回缺失、空评分，不再返回中性代理；真实中性分仍可使用。历史估值分位只表示历史相对位置，不直接等于内在价值高低。
- 公司质量、市场方向、交易动作和账户仓位分开。没有账户规模、已有敞口、风险预算及失效条件时，不生成固定仓位或张数。
- 已持仓者按结构/经营论点管理，新买、回补和净加仓分别检查。压力位、1R/2R 不是自动卖出指令；价投也不能以“长期”绕过偿债风险或被证伪的论点。
- 图上的候选不等于可成交报价。日线、3m、5m 和期权有不同的时点、滑点、费用与数据权限要求。

## 如何使用股票回测

常用技能通过 `get_stock_backtest_summary` 读取当前标的的已有历史报告回测。省略窗口时由工具明确返回选用窗口及选择原因；明确指定窗口时，不静默替换成另一个窗口。

报告需要区分：

1. 方向命中率与做多交易盈利率；空仓零收益不能混入后者。
2. 盈利次数与平均盈亏。较高盈利比例仍可能因平均亏损更大而产生负期望。
3. 总样本、已完成、无法结算、同股重复/重叠观察及引擎版本。
4. 模拟入场/退出与实际成交；未纳入费用、滑点的结果不能当净收益。
5. 股票历史报告表现与某项技能表现。没有可归因数据时，`get_skill_backtest_summary` 明确返回不支持；不得借用总体成绩。

短期股票回测不能证明多年价值投资、日内做 T 或期权策略有效，也不能用短期命中率否定长期投资逻辑。旧结果与当前规则存在重放差异，详见[只读审计证据](ask-stock-backtest-evidence.md)。当前优化收紧数据、风险与披露边界，不依据样本内最高收益调整参数，也不宣传已通过样本外交易验证。

## 验证与回滚

技能定义在 `strategies/`，共享证据约束在 `src/agent/skills/evidence_policy.py`。计算和数据门禁有对应服务/工具测试；UI 分组需同时核对问股及首页入口，并验证旧会话的技能恢复。

Codex 的核心读取与分析工具通过已有 Tool Surface 的独立进程执行。阻塞调用不能仅靠标记 `cancellation_safe` 冒充协作取消；`process_cancellation_safe` 仅对经过核验的读取与事务型缓存写入开放，并要求内部拥有的进程组。行情刷新/历史冻结上下文及成功新闻的证据计数在进程边界保留；失败、取消或结果截断不补报成功新闻。停止后必须回收工具进程，数据库缓存只保留已经完成的事务。普通受控线程、业务写入和模型提供的参数不能获取这项能力。

```bash
./scripts/ci_gate.sh
cd apps/dsa-web
npm ci
npm run lint
npm run build
```

服务需重新加载 Python 代码和技能缓存，浏览器刷新后读取新目录；桌面端复用 Web 构建产物，正式打包仍需单独构建桌面端。回滚时恢复本次技能、服务、API 与 Web 修改并重建静态文件、重启服务；本次不迁移数据库，不删除历史回测或旧技能 ID。

## English compatibility note

The seven core entries are a curated navigation group, not a performance ranking. Advanced skills and saved IDs remain supported. Financial provenance, missing-evidence handling, risk-based sizing, completed-bar confirmation and explicit backtest denominators apply to every skill. Historical report backtests must not be presented as skill-specific or long-term value-investing performance. Chinese and English selector labels are updated together; this topic has no separate translated document.
