# 股票分析能力扩展建议（Roadmap）

## 1. 目标与原则

- 目标：在不破坏当前稳定主流程（数据 fallback → 分析 → 报告 → 通知）的前提下，增强“估值深度 + 市场研判 + 组合决策”。
- 原则：
  - 优先增量扩展，不推翻已有策略与工具体系。
  - 先补“高价值短板”（估值完整度、市场状态识别），再做重型量化模块。
  - 默认 fail-open，单模块失败不阻塞全链路。

---

## 2. 当前能力基线（简版）

- 覆盖市场：A 股 / 港股 / 美股
- 策略层：28 个 Skills 定义 / 26 个用户可见（含五维分析、Serenity 投研、多策略联合、价值投资、情绪、趋势）
- 工具层：23 个 Agent Tools（数据/分析/搜索/市场/回测）
- 估值能力：`get_valuation_percentile` 已上线（A 股完整，美股 partial，港股 unavailable）
- 市场分析：大盘复盘 + 行业排行 + 美股流动性 + conviction 面板

> 结论：框架已经具备“可扩展骨架”，下一步重点是把估值与市场判断做深做实。

---

## 3. P0（优先级最高，建议 2-4 周）

## 3.1 估值模型增强（先做）

1. `跨市场估值分位补齐`
- 内容：补齐美股/港股历史分位（PE/PB/PS 至少 3-5 年），从 partial/unavailable 升级为可用。
- 价值：让价值类策略在三市场都能形成可比较信号。
- 落地建议：新增 `src/services/valuation_market_adapter.py`，统一不同数据源字段映射。

2. `轻量 DCF 模型（FCFF）`
- 内容：给出 base/bull/bear 三情景估值区间，而不是单点估值。
- 价值：报告可解释性提升，适合长期投资与仓位上限判断。
- 落地建议：新增 `src/services/dcf_valuation_service.py` + Agent Tool `get_dcf_valuation`。

3. `相对估值引擎`
- 内容：按行业可比公司输出 EV/EBITDA、PS、PEG 分位与偏离。
- 价值：补足“贵/便宜”在横向比较上的证据。
- 落地建议：新增 `src/services/relative_valuation_service.py`。

## 3.2 市场状态识别

4. `市场风格/状态机（Regime Detector）`
- 内容：把市场自动标记为“趋势/震荡/风险释放/风险偏好回升”等状态。
- 价值：同一策略在不同市场状态下自动调整阈值，减少误判。
- 落地建议：新增 `src/services/market_regime_service.py`，在大盘复盘与问股同时注入。

5. `财报事件风险面板`
- 内容：财报窗口前后波动率、跳空概率、历史同类事件表现。
- 价值：提升“事件驱动场景”的风险提示能力。
- 落地建议：新增 `src/services/earnings_event_service.py`。

---

## 4. P1（中期，建议 1-2 个月）

## 4.1 因子与组合层

1. `多因子评分模型`
- 因子：质量（ROE/毛利）、估值、动量、波动、资金流。
- 输出：统一 0-100 分 + 因子贡献拆解。
- 落地：`src/services/factor_scoring_service.py`。

2. `组合风险引擎升级`
- 指标：VaR/CVaR、最大回撤压力测试、行业集中度、相关性热图。
- 落地：扩展 `portfolio_risk_service.py`。

3. `仓位建议器`
- 内容：结合 conviction、波动率、回撤预算给出仓位区间（如 10%-25%）。
- 落地：`src/services/position_sizing_service.py`。

## 4.2 市场分析增强

4. `行业轮动热力模型`
- 内容：行业相对强弱、动量持续性、资金流共振评分。
- 落地：`src/services/sector_rotation_service.py`。

5. `宏观流动性扩展（FRED 等）`
- 内容：把联储资产负债表、实际利率、信用利差纳入市场面板。
- 落地：`src/services/macro_liquidity_service.py`（可先做可选模块）。

---

## 5. P2（长期，季度级）

1. `策略组合优化器`
- 对多个策略信号做动态权重配置（基于历史表现与当前市场状态）。

2. `概率化预测输出`
- 从“结论句”升级为“概率分布 + 置信区间 + 关键假设”。

3. `执行层模拟`
- 加入滑点、成交量约束、交易成本的真实交易模拟，和回测联动。

---

## 6. 建议先落地的 3 个最小闭环（可直接拆 Issue）

1. **跨市场估值分位补齐**
- 交付：美股/港股 `get_valuation_percentile` 不再是 partial/unavailable。
- 验收：新增单元测试覆盖三市场 5 年分位输出结构。

2. **轻量 DCF Tool + 报告区块**
- 交付：问股和个股报告新增“内在价值区间（Bull/Base/Bear）”。
- 验收：无数据时 fail-open，不阻塞主报告。

3. **市场状态机注入策略阈值**
- 交付：多策略联合在不同 regime 下使用不同阈值模板。
- 验收：回测统计中看到误报率下降或风险收益比改善。

---

## 7. 与现有工程结构的建议映射

- `src/services/`：新增估值/市场/因子类 service（保持与现有服务层一致）。
- `src/agent/tools/`：每个新能力对应一个 ToolDefinition，纳入注册表。
- `strategies/`：如需新策略，新增 YAML 即可被前端自动发现。
- `tests/`：优先补 `service` 与 `tool` 单测；网络依赖统一 mock。
- `docs/`：能力变更同步 `docs/capabilities/README.md` 与 `docs/CHANGELOG.md`。

---

## 8. 风险与注意事项

- 数据质量风险：跨市场字段口径不一致，必须加标准化与置信度标注。
- 解释风险：估值模型参数敏感，报告需披露关键假设（增长率/折现率）。
- 工程风险：避免一次性引入过多外部依赖，先做可选模块并保留 fallback。
- 产品风险：建议把“投资建议”与“数据证据”分栏，降低误读。

---

## 9. 参考文件

- 能力图：`docs/stock-analysis-capability-map/capability-map.html`
- 当前能力总览：`docs/capabilities/README.md`
