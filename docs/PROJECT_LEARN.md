# 自选股智能分析系统 - 项目学习指南

## 项目简介

**自选股智能分析系统** 是一个基于 AI 大模型的股票分析工具，支持 A股、港股、美股 的自动分析与多渠道推送。

### 核心价值

- 每日自动分析自选股，生成「决策仪表盘」
- 一句话核心结论 + 精确买卖点位 + 操作检查清单
- 支持企业微信、飞书、Telegram、Discord、Slack、邮件等多渠道通知

---

## 系统架构

```
daily_stock_analysis/
├── main.py                 # CLI 入口，调度全流程
├── server.py               # FastAPI 服务入口
├── src/                    # 核心业务逻辑
│   ├── core/               # 核心编排（pipeline、scheduler、trading_calendar）
│   ├── agent/              # AI Agent（多策略对话、工具调用）
│   ├── services/           # 业务服务层
│   ├── repositories/       # 数据访问层
│   ├── notification_sender/# 通知渠道发送器
│   ├── schemas/            # 数据结构定义
│   └── utils/              # 工具函数
├── data_provider/          # 多数据源适配（AkShare、Tushare、YFinance、Longbridge）
├── api/                    # FastAPI 路由
├── bot/                    # 机器人接入（钉钉、飞书、Telegram）
├── apps/
│   ├── dsa-web/            # Web 前端（React）
│   └── dsa-desktop/        # Electron 桌面端
├── scripts/                # 工具脚本
└── docs/                   # 文档
```

---

## 核心功能模块

### 1. 数据采集 (`data_provider/`)

| 数据类型 | 数据源 | 说明 |
|---------|-------|------|
| A股行情 | efinance → AkShare → Tushare → Pytdx → Baostock | 按优先级自动降级 |
| 港股行情 | AkShare（EM/Sina）→ Tushare → efinance | 长桥可作为首选 |
| 美股行情 | Longbridge → YFinance | 长桥优先策略 |
| 新闻搜索 | Tavily、SerpAPI、Bocha、Brave、MiniMax、SearXNG | 多源聚合 |
| 社交舆情 | Stock Sentiment API（Reddit/X/Polymarket）| 仅美股 |

**长桥优先策略**：配置 `LONGBRIDGE_APP_KEY/SECRET/TOKEN` 后，美股/港股日线K线与实时行情由长桥优先拉取；失败时自动降级到 YFinance/AkShare。

### 2. 技术分析 (`src/core/pipeline.py`)

- **MA 指标**：MA5/MA10/MA20 多头排列判断
- **乖离率**：超过阈值（默认 5%）提示不追高风险
- **筹码分布**：筹码集中度分析
- **实时行情**：价格、涨跌幅、成交量、换手率

### 3. AI 分析 (`src/agent/`)

基于 LiteLLM 的多模型统一调用，支持：
- Gemini、GPT、Claude、DeepSeek、Qwen、Ollama
- 多 Key 负载均衡
- 多渠道 fallback

**Agent 架构**：
```
用户问题 → Technical Agent → Intel Agent → Risk Agent → Specialist Agent → Decision Agent
                          ↓
                    多 Agent 级联编排（可配置深度：quick/standard/full/specialist）
```

**内置策略（Skills）**：
- 均线金叉、缠论、波浪理论、多头趋势等 11 种
- 支持自定义策略（YAML 配置）

### 4. 报告生成 (`src/services/`)

**决策仪表盘**包含：
- 一句话核心结论
- 精确买卖点位（买入价、止损价、目标价）
- 操作检查清单（满足/注意/不满足）
- 舆情情报与风险警报

**报告类型**：
- `simple` - 精简版
- `full` - 完整版（Docker 推荐）
- `brief` - 3-5 句概括

### 5. 通知推送 (`src/notification_sender/`)

| 渠道 | 配置项 |
|------|--------|
| 企业微信 | `WECHAT_WEBHOOK_URL` |
| 飞书 | `FEISHU_WEBHOOK_URL` |
| Telegram | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| Discord | `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` |
| Slack | `SLACK_BOT_TOKEN` 或 `SLACK_WEBHOOK_URL` |
| 钉钉 | `CUSTOM_WEBHOOK_URLS` |
| 邮件 | `EMAIL_SENDER` + `EMAIL_PASSWORD` + `EMAIL_RECEIVERS` |
| PushPlus | `PUSHPLUS_TOKEN` |
| Server酱 | `SERVERCHAN3_SENDKEY` |

### 6. Web 工作台 (`apps/dsa-web/`)

- **首页**：历史分析记录、分析入口
- **问股**：Agent 策略对话（多轮）
- **回测**：AI 预测 vs 次日实际 准确率评估
- **持仓**：股票持仓管理
- **设置**：API Key、通知渠道、调度时间配置

**双主题**：浅色/深色主题切换，移动端适配

---

## 业务流程

```
┌─────────────────────────────────────────────────────────────┐
│                      每日定时 / 手动触发                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  1. 数据采集                                                 │
│     - 行情数据（分市场多源 fallback）                          │
│     - 新闻搜索（多源聚合）                                    │
│     - 社交舆情（美股）                                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  2. 技术分析                                                 │
│     - MA 多头排列判断                                        │
│     - 乖离率计算                                            │
│     - 筹码分布                                              │
│     - 实时行情                                              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  3. AI 分析（Agent）                                         │
│     - 多 Agent 级联编排                                      │
│     - 技术分析 → 情报整合 → 风险评估 → 策略生成 → 决策输出    │
│     - 支持多策略（缠论/波浪/均线金叉等）                       │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  4. 报告生成                                                 │
│     - 决策仪表盘                                             │
│     - 操作检查清单                                           │
│     - 风险警报 / 利好催化                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  5. 通知推送                                                 │
│     - 多渠道同时推送                                         │
│     - 支持单股推送 / 合并推送                                 │
│     - Markdown 转图片（可选）                                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  6. 飞书云文档（可选）                                        │
│     - 自动创建大盘复盘文档                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  7. 自动回测（可选）                                          │
│     - 基于历史分析评估准确率                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 方式一：GitHub Actions（推荐，零成本）

1. Fork 仓库
2. 配置 Secrets（至少需要 `STOCK_LIST` 和一个 AI API Key）
3. 启用 Actions
4. 手动触发测试

### 方式二：本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
vim .env

# 运行分析
python main.py

# 启动 Web 服务
python main.py --webui
```

### 方式三：Docker 部署

```bash
docker build -t daily-stock-analysis .
docker run -d --env-file .env daily-stock-analysis
```

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `python main.py` | 正常运行 |
| `python main.py --debug` | 调试模式 |
| `python main.py --dry-run` | 仅获取数据不分析 |
| `python main.py --stocks 600519,AAPL` | 指定股票 |
| `python main.py --market-review` | 仅大盘复盘 |
| `python main.py --schedule` | 定时任务模式 |
| `python main.py --webui` | 启动 Web 界面 |
| `python main.py --backtest` | 运行回测 |
| `python main.py --backtest-code 600519` | 回测指定股票 |

---

## 配置指南

### AI 模型（至少配置一个）

```env
# 推荐：AIHubMix（一键使用 Gemini/GPT/Claude/DeepSeek）
AIHUBMIX_KEY=your_key

# 或：Gemini（免费额度）
GEMINI_API_KEY=your_key

# 或：DeepSeek（性价比高）
DEEPSEEK_API_KEY=your_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
```

### 通知渠道（至少配置一个）

```env
# 企业微信
WECHAT_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx

# 飞书
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx

# Telegram
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# 邮件
EMAIL_SENDER=xxx@qq.com
EMAIL_PASSWORD=xxx
EMAIL_RECEIVERS=xxx@example.com
```

---

## 关键配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `STOCK_LIST` | 自选股列表（逗号分隔） | - |
| `REPORT_TYPE` | 报告类型：simple/full/brief | `simple` |
| `REPORT_LANGUAGE` | 输出语言：zh/en | `zh` |
| `MAX_WORKERS` | 并发线程数 | `3` |
| `SINGLE_STOCK_NOTIFY` | 单股推送模式 | `false` |
| `MERGE_EMAIL_NOTIFICATION` | 邮件合并推送 | `false` |
| `TRADING_DAY_CHECK_ENABLED` | 交易日检查 | `true` |
| `BIAS_THRESHOLD` | 乖离率阈值（%） | `5.0` |
| `AGENT_MODE` | 开启 Agent 问股 | `false` |
| `NEWS_MAX_AGE_DAYS` | 新闻最大时效（天） | `3` |

---

## 进阶功能

### Agent 策略问股

设置 `AGENT_MODE=true` 后可访问 `/chat` 页面进行多轮策略问答：

- 选择策略（均线金叉/缠论/波浪等）
- 自然语言提问
- 流式进度反馈
- 导出与发送

### 回测验证

- 按股票和分析日期查看"AI 预测 vs 次日实际"
- 评估准确率
- 支持按时间范围筛选

### 智能导入

- **图片**：拖拽截图，Vision AI 识别股票代码
- **CSV/Excel**：批量导入
- **剪贴板**：粘贴表格数据

### 飞书云文档

配置飞书 Webhook 后，自动创建每日大盘复盘文档

---

## 目录结构详解

```
src/
├── core/                  # 核心编排
│   ├── pipeline.py       # 个股分析流水线
│   ├── market_review.py   # 大盘复盘
│   ├── config_registry.py # 配置注册表
│   ├── trading_calendar.py # 交易日历
│   └── backtest_engine.py  # 回测引擎
├── agent/                # AI Agent
│   ├── agents/           # 多类 Agent（Technical/Intel/Risk/Decision）
│   ├── skills/           # 策略技能
│   ├── tools/            # 工具（行情/搜索/回测）
│   └── executor.py       # Agent 执行器
├── services/             # 业务服务
│   ├── analysis_service.py
│   ├── backtest_service.py
│   ├── portfolio_service.py
│   └── stock_service.py
├── repositories/         # 数据访问
│   ├── analysis_repo.py
│   ├── backtest_repo.py
│   └── portfolio_repo.py
├── notification_sender/  # 通知渠道
│   ├── wechat_sender.py
│   ├── feishu_sender.py
│   ├── telegram_sender.py
│   ├── discord_sender.py
│   ├── slack_sender.py
│   └── email_sender.py
└── schemas/              # 数据结构
    └── report_schema.py

data_provider/            # 多数据源适配
├── akshare_provider.py
├── tushare_provider.py
├── yfinance_provider.py
├── longbridge_provider.py
└── base.py               # 统一接口

api/                      # FastAPI
├── app.py
├── routers/
│   ├── analysis.py
│   ├── stocks.py
│   └── usage.py
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [完整指南](full-guide.md) | 详细部署与配置 |
| [LLM 配置指南](LLM_CONFIG_GUIDE.md) | AI 模型配置详解 |
| [常见问题](FAQ.md) | FAQ |
| [更新日志](CHANGELOG.md) | 版本历史 |
| [贡献指南](CONTRIBUTING.md) | 开发贡献 |

---

## 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。
