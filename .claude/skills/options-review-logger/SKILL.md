# Options Review Logger

把“从券商截图读取期权记录 -> 去重写入 Excel -> 刷新盈亏复盘”做成一次性可复用流程。

## 何时使用

当用户提到以下任意诉求时，必须使用本 skill：

- “把这批期权截图记到 Excel”
- “帮我做去重后入账”
- “同步刷新策略复盘/胜率/周度到期统计”
- “按之前格式继续记期权”
- **直接发送券商期权持仓/到期截图并要求总结盈亏**（默认走完整入账流程，不只口头汇总）

尤其当输入包含 `docs/options-pic/` 下图片和 `docs/options-review/期权记录_2026.xlsx` 时，优先走本 skill，而不是手工改表。

## 默认用户偏好

除非用户明确说“只总结、不入账”，否则收到期权截图后默认执行：

1. 从截图提取结构化记录
2. 写入 `docs/options-review/期权记录_2026.xlsx` 的 `交易明细`
3. **刷新 `策略复盘` 统计**（总览 / 胜率 / 周度到期摘要）
4. 回复简短盈亏汇总 + 脚本 `added / skipped` 结果

### 默认结算规则

- 今日到期且 OTM 归零的卖方合约：按 **100% 权利金** 结算（`settle_full_premium: true`）
- 价内到期指派（接股 / 被 call 走）：按截图或市价计算实际 `pnl`，备注写清指派细节
- 未说明时，盈利方优先按 100% 权利金入账（与用户口头“按 100% 算”一致）

### 分析时的趋势与事件风控（与 income-scanner 一致）

用户询问**新开仓 / 是否卖 Put·Call / 收租**时，除入账外应同步检查 `options-income-scanner`：

- **「单边下跌趋势（收租硬性风控）」**：写明 `Trend regime`；单边下跌 + 高 IV → 默认不推荐常规卖 Put
- **「关键事件节点（收租硬性风控）」**：核对持仓期内财报 / FOMC / CPI / NFP / 除息；`event risk: blocked` → 默认 Skip 或建议换到期日
- **「支撑位 / 阻力位（卖 Put·Call 硬性风控）」**：写明 `Support` / `Resistance` / `Price location`；**支撑附近不卖 Call、可考虑卖 Put**；**阻力附近不卖 Put、可考虑卖 Call**

指派/接股类亏损入账时，备注可引用趋势、事件或支撑失守（如「跌破支撑后价内指派」）

## 输入与产出

### 输入

1. 图片路径列表（可多张）
2. 目标 Excel 路径（默认 `docs/options-review/期权记录_2026.xlsx`）
3. 规则补充（例如：`今天到期卖Put/卖Call按OTM=100%结算`）

### 产出

1. 更新后的 `交易明细`：新增分段、去重入账、样式一致
2. 更新后的 `策略复盘`：
   - 已结算总览
   - Spread 净盈亏汇总（可选增量）
   - 卖方周度到期摘要
3. 控制台 JSON 结果：`added / skipped / workbook`

## 执行步骤

### Step 1: 读取图片并提取结构化数据

逐张读取图片（必要时局部放大），提取为 JSON：

- 标的（ticker）
- 操作（卖Call / 卖Put / 买Call / 买Put）
- 行权价
- 到期日
- 权利金（若可识别）
- 平仓/结算日期
- 实际盈亏（若可识别）
- 备注（佣金、截图显示已实现盈亏、是否当日开平等）

把结果写到一个本地 JSON 文件，格式参考：

- `.claude/skills/options-review-logger/templates/options_updates.example.json`

### Step 2: 处理“今日到期 OTM=100%结算”规则

如果用户明确给出“今日到期卖方可按 100% 拿到权利金”：

- 对对应记录设置 `settle_full_premium: true`
- 并确保 `premium` 有值
- 脚本会自动将 `pnl= premium`、`pnl_pct=100%`、`close_date=expiry(若未填)`

### Step 3: 执行同步脚本

```bash
python .claude/skills/options-review-logger/scripts/options_review_sync.py \
  --workbook docs/options-review/期权记录_2026.xlsx \
  --input <your_json_file>
```

### Step 4: 结果核对（必须）

至少核对：

1. `交易明细` 新增区块标题与编号连续
2. 新增行样式与历史行一致（尤其 `操作` 列、金额列）
3. `策略复盘` 的统计值是否同步变化
4. 脚本输出里的 `skipped` 是否符合预期（去重命中）

## 去重规则

脚本默认按以下 key 去重（任意一项变化视为新记录）：

- `ticker`
- `action`
- `strike`
- `expiry`
- `close_date`
- `pnl`

这能覆盖“同一合约重复截图”场景；如果同一合约分批平仓导致 `pnl` 不同，会被保留为不同记录。

## Spread 汇总更新（可选）

如果本次有新增 Spread 净值，在 JSON 里填写 `spread_review_rows`，脚本会合并到 `策略复盘` 的 Spread 汇总并重算合计。

## 注意事项

- 本 skill 默认只处理本仓库 Excel 模板结构（`交易明细` + `策略复盘`）
- 如果表结构发生变更，先做 dry-run 复制文件验证
- 提取不完整时，允许先写 `-`，但备注必须写清楚“截图待补字段”
