---
name: bingshen-volume-price-analysis
description: >-
  A-share swing trading analysis using the Bingshen (冰封之谷) volume-price framework:
  volume expansion/contraction, washout vs distribution, N-pattern entries, double-top
  neckline, rounding bottom, and sector-anchor risk (e.g. optical modules). Use when
  the user mentions 冰神、冰封之谷、量价关系、N字买法、洗盘、出货、放量阴线、缩量涨、
  量价背离、圆弧底、双头、多空线、硬顶诱多, or asks to judge A-share technical
  structure from daily OHLCV. Outputs structured diagnostic reports; not auto-trading advice.
---

# Bingshen Volume-Price Analysis（冰神量价框架）

将「冰封之谷」聊天中可复现的 **量价结构逻辑** 转为可执行的分析工作流。定位为 **启发式技术面辅助**，与 `five-dimension-stock-analysis` 的技术面子模块互补，**不替代**基本面或多维共振。

> 方法论来源：`docs/proReport/bingshen-chat-summary.md`、`docs/proReport/冰神聊天.rtf`
> 量化阈值与决策树：`references/rules.md`
> 校准案例：`references/calibration-cases.md`

## 何时使用

用户出现以下任一意图时必须启用本 skill：

- 判断某 A 股是 **洗盘还是出货**
- 评估 **N 字买法** 买点/止损是否合理
- 检查 **高位放量大阴线**、**缩量涨**、**量价背离**
- 板块锚定风险（如光模块看中际旭创）
- 要求按「冰神」「冰封之谷」风格做技术面复盘

**不适用**：纯美股/港股若缺少 A 股量价语境；用户明确要求全自动下单；用户仅要富途公式 → 转 `futu-indicator-writer`。

## 核心原则（执行前必读）

1. **量价优先**：先量价结构，再形态命名；业绩/消息在短线语境下降级为背景。
2. **危险信号优先**：高位放量大阴线、缩量涨、该缩不缩 → 覆盖「看起来像洗盘」的乐观解读。
3. **历史对照**：量价关系混乱时，必须拉 **同股更长历史**（建议 ≥120 交易日）对照「前面作业」。
4. **置信度分层**：量化扫描 = 客观信号；庄家/诱多/硬顶 = 低置信定性，须标注「需看图确认」。
5. **不做强买强卖**：输出「等反包 / 观望 / 结构破坏」；禁止给出无止损的追涨建议。
6. **免责声明**：每次完整报告末尾附非投资建议声明。

## 工作流

### Step 0：澄清输入

| 字段 | 默认 |
|------|------|
| 标的 | 必填；支持 `600519`、`SZ:002851`、`hk00700`（港股量价语境弱） |
| 周期 | 日线 |
| 持有窗口 | 波段 2 天–8 周 |
| 历史长度 | 120 交易日（形态对照需 60+） |
| 板块锚 | 用户提及光模块/AI 硬件时，额外看中际旭创等相关龙头 |
| 筹码 | 有则增强；无则用 `DataFetcherManager` 近似筹码分布 |

信息不足时最多追问 2 个问题，否则按上表默认并在报告中注明假设。

### Step 1：获取数据

**优先顺序**：

1. 仓库内：`src.services.history_loader.load_history_df(code, days=120)`
2. Agent 工具：`get_daily_history`
3. 用户截图：仅作定性补充，价格/量能结论须用数据复核

可选板块锚：对锚定标的重复 Step 1。

**筹码（可选）**：`DataFetcherManager` 的近似 volume-profile（见 `data_provider/base.py` `_build_approx_chip_distribution`）。标注 `source=approx`，不得伪装为通达信实盘筹码。

### Step 2：运行量化扫描（推荐）

从仓库根目录执行：

```bash
python3 .claude/skills/bingshen-volume-price-analysis/scripts/vp_scanner.py \
  --codes 300390,002851,688498 \
  --days 120 \
  --format markdown
```

扫描器输出 **信号表 + 建议倾向**，不是最终结论。Agent 必须结合 Step 3–4 做语义收敛。

### Step 3：逐条量价诊断（7 铁律）

对每条规则标定：`✅满足` / `❌不满足` / `⚠️模糊`，并附置信度（高/中/低）。规则定义见 `references/rules.md` §1。

| # | 规则 |
|---|------|
| R1 | 上涨放量、下跌缩量（健康量价） |
| R2 | 大阴线后能否被大阳线快速包住 |
| R3 | 是否存在高位放量大阴线（谨慎锚点日） |
| R4 | 是否存在缩量涨 / 量价背离 |
| R5 | 回调是否缩量且底部未破 |
| R6 | 是否出现长阴短柱（惜筹） |
| R7 | 二波/新高是否量价齐包 |

### Step 4：形态与主力意图判定

按 `references/rules.md` §2 决策树输出 **主判定**（单选）：

- `洗盘蓄势` / `出货分发` / `假弃庄观察` / `高位风险` / `结构破坏` / `观望待确认`

**强制规则**：

- 双头：**仅**在跌破颈线后确认；未破不判死刑。
- N 字买法：仅当 R1/R5 偏正面 + 有放量阳线模板时给出买点/止损区间。
- 麦格式异常：洗盘语境下 **该缩却放量** → 主判定不得为「纯洗盘」。
- 阶梯式出货：近端急跌且缺少横盘段 → 非「洗到底」。

### Step 5：构建交易计划（条件式）

仅当主判定为 `洗盘蓄势` 或用户明确要求入场规划时填写：

| 字段 | 说明 |
|------|------|
| 多空线 / 支撑 | 圆弧底低点或两次回踩确认位 |
| N 字买点 | 放量阳线实体 1/2 附近 |
| 止损 | 放量阳线底部（约半根 K 风险） |
| 触发加仓 | 量能反包、量价齐包 |
| 失效 | 跌破多空线/圆弧底/颈线 |

仓位与盈亏比：强调「庄家吃三倍你吃一倍」「呆米—不动不做」；不给具体仓位百分比除非用户有账户约束。

### Step 6：输出

**聊天框**：15–25 行摘要（主判定 + 3 条关键证据 + 1 条最大风险 + 行动倾向）。

**完整报告**：保存本地 Markdown：

- 路径：`research/{代码}/{代码}_冰神量价_{YYYY-MM-DD}.md`
- 环境无法写文件时跳过，但聊天摘要仍必须输出。

使用 `references/report-template.md` 结构。

### Step 7：可选联动

- 用户要富途副图信号 → `futu-indicator-writer`（N 字、放量阴线、背离）
- 用户要多维共振 → `five-dimension-stock-analysis`（本 skill 只填「技术面-量价」子维）

## 冲突消解（重要）

多条规则打架时，按优先级：

1. 高位放量大阴线（R3）+ 缩量涨（R4）
2. 该缩不缩的放量跌（麦格式）
3. 跌破圆弧底 / 颈线
4. 涨放量跌缩量（R1）+ 底部未破（R5）
5. 历史同股「作业」相似度（定性）

## 输出禁忌

- 不得把扫描器 `lean` 字段直接写成「建议买入」
- 不得在无日线数据时编造量能数字
- 不得将近似筹码当作实盘通达信筹码下结论
- 不得输出「必涨」「必跌」

## 参考文件

- 规则与阈值：`references/rules.md`
- 校准案例：`references/calibration-cases.md`
- 报告模板：`references/report-template.md`
- 方法论文档：`docs/proReport/bingshen-chat-summary.md`
