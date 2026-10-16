# 问股日线波段执行计划 / Daily swing execution plans

## 边界

`daily-swing-v1` 将方向评分与可执行交易机会分开。13项明细、五维评分、证据质量与颜色继续保留；分数不是上涨概率。默认日线决定首仓与失效，周线提供背景，日内VWAP缺失不否定完整日线证据。事件未核验必须明确标注，不等于没有事件风险。

没有自动交易、没有完整金字塔收益证明，也没有改动模型或行情供应商。缺账户总风险预算时只显示公式，不编造股数。初次风险份额指**本笔总损失预算**，不是账户仓位百分比。

## 三类候选与风险

| 类型 / 配置值 | 已完成日线识别 | 首次风险份额 |
|---|---|---:|
| 顺势回踩 `trend_pullback` | 已确认HH/HL；最近3根测试MA20或确认HL的0.5ATR范围；阳线收盘超过前高 | 25% |
| 初次突破 `initial_breakout` | 阳线收盘越过此前20日高点，收盘位于振幅上方25% | 25% |
| 底部反转 `bottom_reversal` | 扫破此前20日低点，阳线收回，收盘位于振幅上方25% | 12.5% |

同一时点仅选择一个主计划，按表内顺序优先。五根K线摆动点必须等右侧两根收盘后确认；已被后续低点破坏的结构不复用。默认硬止损为重要低点减0.5ATR，反转使用当根扫低低点。只以价格上方真实历史高点作目标参考。

`R = 拟成交价 - 硬止损`。有真实目标时最低计划RR=1.5，最高买价为 `(目标+1.5×止损)/2.5`，再取价格延伸上限较低者：回踩/反转为信号收盘+0.5ATR，突破为突破位+1ATR。突破无可验证上方目标时，目标与RR不适用，采用结构跟踪；其他类型缺目标不启用。信号已超价则本计划取消，不事后恢复。

计划只对下一交易日有效；当前价格还须带匹配标的与可验证供应商时间戳。入场价改变时同步重算R、股数和1R/2R；1R/2R只是观察位，不机械止盈。首仓盈利并出现新确认结构后才能另做加仓风控，累计风险上限25%→50%→100%；底部反转确认HL并转多前上限12.5%。禁止亏损摊平。

## 接口与记录

计分工具保留旧参数/返回字段，追加 `stock_code` 和 `timeframe="daily"`；没有标的与可信快照的旧调用不能开启新类型。服务端通过现有history loader强制刷新日线，核对完成日期、交易日缺口、OHLC和复权声明，生成版本化 `trade_plan`。Futu/Longbridge声明前复权，YFinance声明拆股分红调整；未经声明的数据源只降级展示，不能补造价格。

计划包含代码、版本、来源、OHLC快照哈希、完成日期、获取时点、有效交易日、触发类型、入场上下限、失效位、风险份额、取消原因及验证状态。LiteLLM与Codex只传递本轮工具结果；股票范围不匹配、取消、失败、超时不记成功决策。不能从模型正文重建新计划。

`DecisionSignal` 使用原字段和metadata保存不可回溯快照、会话和消息关联。观望也记录；同一会话输出重复写入返回原快照，写入失败记录日志但不拖垮问股。无破坏性迁移，不重算历史报告。表现中心可展开最近问股计划及逐笔结算对应原计划；**方向命中率不是交易胜率**，首仓模拟单独交付，不写入旧方向统计。

问股表格保留彩色加粗重点，窄屏采用表内横向滚动和最小标题列宽，避免关键字段被挤成逐字换行；滚动区域可通过键盘聚焦。

模型正文的独立执行段落会被过滤并显示省略提示，不从正文提取新买卖价。持仓边界不能继承模型自填止损来自动清仓。PA工具入口同样排除未完成日线。长桥报价保留SDK真实timestamp；依据[官方SDK时间转换实现](https://github.com/longbridge/openapi/blob/master/python/src/time.rs)，无时区datetime表示主机本地时间，转换为带UTC时区的ISO值，不以请求时间补造。缺timestamp仍拒绝立即入场。

## 配置、启用与回滚

```dotenv
# 默认空；仅在独立验收通过后填写逗号分隔的类型
MULTI_STRATEGY_ENABLED_SETUPS=
```

空列表使用P0修正后的原入场政策，并展示新候选的未启用状态。非空时仅名单内类型可以给出技术试仓建议；名单外候选不得启用。配置不代表统计显著性，管理员须保留对应验收产物。未知类型拒绝，不静默启用。杠杆ETF不在首版验收范围，不能套用普通股票首仓。

清空列表、按现有 `python main.py --serve-only` 入口重启即可回滚新增买点政策；保留P0修复（动作一致、正确阻力、取消固定3%否决、R一致）、审计记录和历史快照。修改配置与Python逻辑需重启，前端改动需重新build。重启前确认没有问股请求在执行。

## 固定历史回放

```bash
# 使用新的本地验收目录，抓取一次后冻结CSV与哈希
.venv/bin/python scripts/replay_swing_entry_plans.py --output reports/swing-check-YYYYMMDD --fetch
# 同一快照离线重现，不加--fetch
.venv/bin/python scripts/replay_swing_entry_plans.py --output reports/swing-check-YYYYMMDD
```

固定ORCL、AXTI、RKLB、AMKR、INTC、MSTR、NFLX、SOFI、WULF、RXRX、BABA、CPNG，不按结果换股。至少10只有效。前120根预热，随后前70%开发检查、后30%留出；边界剔除跨段20日窗口，最后20根保留作结算。参数不按留出结果调整。

只用信号时点及以前数据，下一开盘区间内成交，越界不成交；硬止损跳空取更差开盘价；日内先后不明按不利顺序标注。结构止损只上移并于下一根生效。主10日、补充5/20日，期末按收盘估值。费用每边10bps，压力每边20bps，均为假设。每股票同策略/窗口同时最多一仓。单位风险序列按入场日与代码排序；其最大回撤不是多股组合NAV回撤。

按类型独立要求：样本外≥30笔非重叠成交且≥5股；基准均净R>0、盈利因子>1；压力均净R≥0；序列最大回撤≤6R；同候选净R/机会不差于旧门控。旧门控用原MA/20日高点及固定3%规则，对照注入同一结构止损、入场窗口与成本；**缺少历史13项评分，不能解释成完整AI策略比较**。旧规则成交不足要披露。

输出 `protocol.json`（参数与代码哈希）、各股CSV和`snapshots.json`、`summary.json`、`trades.json/csv`（含未成交原因、退出、净R、MAE/MFE、止损路径）、`report.md`。截图和运行日志放同一忽略目录，不合入Git。自选池选择偏差、现取复权历史并非完整点时库、事件未回放、未检验加仓策略均须保留披露。

## English summary

This document is bilingual at the contract level. Daily completed bars determine entries and invalidation; weekly context and intraday VWAP are separate evidence. Three setup types are independently gated and disabled by default. Risk fractions allocate a campaign loss budget, not account equity. Unknown budgets never produce share counts. Repricing recomputes all R-based fields.

The server creates a versioned, symbol-bound snapshot. Both agent backends pass current-turn structured output directly to rendering and persistence. Watch decisions are retained; cancellation, timeout, failure and symbol mismatch cannot create successful decisions. Existing direction-hit statistics are not execution win rates. No destructive migration or automated trading is introduced.

The fixed 12-symbol replay freezes history, uses a 120-bar warmup, a chronological 70/30 split with boundary purge, next-open fills, adverse gap stops, ratcheting structure stops, 5/10/20-day windows and 10/20-bps-per-side costs. Each setup needs 30 holdout trades over 5 symbols, positive baseline mean R and profit factor above one, nonnegative stressed mean R, maximum sequence drawdown at most 6R, and no worse net R per opportunity than the controlled old gate. This is an execution-layer comparison, not a reconstruction of historical AI scores or proof of future profitability. Roll back by clearing `MULTI_STRATEGY_ENABLED_SETUPS` and restarting; P0 correctness fixes and historical snapshots remain.
