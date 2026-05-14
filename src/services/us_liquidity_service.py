# -*- coding: utf-8 -*-
"""
US Market Liquidity Service
============================
从 Yahoo Finance（yfinance）拉取美股资金流动性核心指标，
格式化为大盘复盘报告中可附加的 Markdown 段落。

覆盖指标（Tier 1，零依赖、零密钥）：
- VIX（恐慌指数）：风险偏好
- ^MOVE（MOVE 债市波动率）：债市紧张程度
- ^TNX（10 年期美债收益率，单位 %）：利率方向 / 流动性松紧
- DX-Y.NYB（美元指数 DXY）：全球美元流动性
- HYG（高收益债 ETF）：信用利差代理

无需认证。数据源不可用时静默跳过，不影响主流程（fail-open）。

下一档进阶（Tier 2，FRED）：联储净流动性、HY OAS、收益率曲线等。
"""

import logging
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800  # 30 分钟
_DEFAULT_LOOKBACK_DAYS = 7  # 取 7 个日历日，确保至少覆盖 5 个交易日

# 指标定义：(yf_symbol, 中文显示名, 单位, 信号类型)
# 信号类型用于决定 5 日变化的比较口径：
#   - "level"：直接比绝对值（VIX/MOVE 这类有自然阈值的）
#   - "rate_bp"：利率，5 日变化按 basis point 比较（TNX）
#   - "pct"：5 日 % 变化（DXY/HYG）
_INDICATORS: List[Dict] = [
    {
        "key": "vix",
        "symbol": "^VIX",
        "name": "VIX 波动率",
        "unit": "",
        "signal_type": "level",
    },
    {
        "key": "move",
        "symbol": "^MOVE",
        "name": "MOVE 债市波动率",
        "unit": "",
        "signal_type": "level",
    },
    {
        "key": "tnx",
        "symbol": "^TNX",
        "name": "10Y 美债收益率",
        "unit": "%",
        "signal_type": "rate_bp",
    },
    {
        "key": "dxy",
        "symbol": "DX-Y.NYB",
        "name": "美元指数 (DXY)",
        "unit": "",
        "signal_type": "pct",
    },
    {
        "key": "hyg",
        "symbol": "HYG",
        "name": "HYG 高收益债",
        "unit": "",
        "signal_type": "pct",
    },
]


def _classify_signal(key: str, signal_type: str, current: float, change: float) -> Tuple[str, str]:
    """
    根据指标类型与当前值/5日变化给出信号与中文解读。

    Returns:
        (emoji, 解读文字)，emoji 取 🟢/🟡/🔴。
    """
    if key == "vix":
        if current < 20:
            return "🟢", "风险偏好回升"
        if current < 30:
            return "🟡", "波动率温和"
        return "🔴", "市场恐慌"

    if key == "move":
        if current < 100:
            return "🟢", "债市稳定"
        if current < 150:
            return "🟡", "债市偏紧"
        return "🔴", "债市恐慌"

    if signal_type == "rate_bp":
        # change 单位为 %（如 0.12 表示 12bp）
        bp = change * 100.0
        if bp < -10:
            return "🟢", "利率下行（流动性宽松）"
        if bp > 10:
            return "🔴", "利率上行（流动性收紧）"
        return "🟡", "利率横盘"

    if signal_type == "pct":
        # change 单位为 %（如 -0.8 表示 -0.8%）
        if key == "dxy":
            if change < -1:
                return "🟢", "美元走弱（风险资产受益）"
            if change > 1:
                return "🔴", "美元走强（风险资产承压）"
            return "🟡", "美元横盘"
        if key == "hyg":
            if change > 1:
                return "🟢", "信用利差收窄（风险偏好回升）"
            if change < -1:
                return "🔴", "信用利差走阔（风险厌恶）"
            return "🟡", "信用利差稳定"

    return "🟡", "中性"


class USLiquidityService:
    """
    美股资金流动性服务。

    拉取 yfinance 上的 5 个核心流动性指标，输出可附加到复盘报告的 Markdown 块。

    用法::

        svc = USLiquidityService()
        block = svc.get_liquidity_block()
        if block:
            report += "\\n\\n" + block
    """

    def __init__(self, lookback_days: int = _DEFAULT_LOOKBACK_DAYS):
        self._lookback_days = max(lookback_days, 3)
        self._cache: Dict[str, tuple] = {}  # date_str → (timestamp, data)

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def get_liquidity_data(self) -> Optional[Dict[str, Dict]]:
        """
        获取最新流动性数据（带 30 分钟 TTL 缓存）。

        Returns:
            { key → {name, current, prev_5d, change, change_pct, unit, signal, signal_text} }
            完全无数据时返回 None；部分指标失败时仍返回字典（缺失项 status='unavailable'）。
        """
        today = date.today().isoformat()
        now = time.monotonic()
        cached = self._cache.get(today)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        data = self._fetch()
        if data is None or not any(item.get("current") is not None for item in data.values()):
            return None
        self._cache[today] = (now, data)
        return data

    def get_liquidity_block(self) -> Optional[str]:
        """
        返回格式化的 Markdown 块，可直接附加到大盘复盘报告。
        数据不可用时返回 None。
        """
        data = self.get_liquidity_data()
        if data is None:
            return None
        return self._format(data)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------
    def _fetch(self) -> Optional[Dict[str, Dict]]:
        """
        拉取所有指标 5+1 日历史，返回结构化结果。
        """
        try:
            import yfinance as yf  # noqa: WPS433
        except ImportError:
            logger.warning("yfinance 未安装，跳过美股流动性面板")
            return None

        results: Dict[str, Dict] = {}
        for spec in _INDICATORS:
            key = spec["key"]
            results[key] = {
                "name": spec["name"],
                "unit": spec["unit"],
                "current": None,
                "prev_5d": None,
                "change": None,
                "change_pct": None,
                "signal": "—",
                "signal_text": "数据不可用",
            }
            try:
                ticker = yf.Ticker(spec["symbol"])
                hist = ticker.history(period=f"{self._lookback_days}d", interval="1d")
                if hist is None or hist.empty or "Close" not in hist.columns:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) < 2:
                    continue

                current = float(closes.iloc[-1])
                # 取约 5 个交易日前的收盘价
                idx = -6 if len(closes) >= 6 else 0
                prev = float(closes.iloc[idx])

                change_abs = current - prev
                change_pct = (change_abs / prev * 100.0) if prev else 0.0

                if spec["signal_type"] == "rate_bp":
                    # rate change in %, 取绝对差（^TNX 已是百分数，如 4.42 表示 4.42%）
                    sig_change = change_abs
                else:
                    sig_change = change_pct

                emoji, text = _classify_signal(
                    key=key, signal_type=spec["signal_type"], current=current, change=sig_change
                )

                results[key].update(
                    {
                        "current": round(current, 2),
                        "prev_5d": round(prev, 2),
                        "change": round(change_abs, 2),
                        "change_pct": round(change_pct, 2),
                        "signal": emoji,
                        "signal_text": text,
                    }
                )
                logger.debug("US liquidity: %s = %.2f (Δ %.2f)", spec["symbol"], current, change_abs)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "US liquidity: %s 拉取失败: %s", spec["symbol"], exc
                )
        return results

    @staticmethod
    def _format(data: Dict[str, Dict]) -> str:
        """
        将 _fetch 输出格式化为 Markdown 面板。
        """
        rows: List[str] = []
        green = yellow = red = 0
        for spec in _INDICATORS:
            key = spec["key"]
            item = data.get(key) or {}
            current = item.get("current")
            change = item.get("change")
            change_pct = item.get("change_pct")
            signal = item.get("signal", "—")
            signal_text = item.get("signal_text", "—")

            if current is None:
                rows.append(
                    f"| {item.get('name', spec['name'])} | — | — | — | 数据不可用 |"
                )
                continue

            unit = item.get("unit") or spec["unit"]
            current_str = f"{current:.2f}{unit}"

            if spec["signal_type"] == "rate_bp":
                bp = (change or 0) * 100.0
                change_str = f"{bp:+.0f}bp"
            elif spec["signal_type"] == "pct":
                change_str = f"{change_pct:+.2f}%"
            else:
                change_str = f"{change:+.2f}"

            rows.append(
                f"| {item.get('name', spec['name'])} | {current_str} | {change_str} | {signal} | {signal_text} |"
            )

            if "🟢" in signal:
                green += 1
            elif "🔴" in signal:
                red += 1
            elif "🟡" in signal:
                yellow += 1

        report_date = date.today().isoformat()
        if green + yellow + red == 0:
            verdict = "未取得有效数据，建议关注 yfinance 网络状况。"
        elif green >= red + 2:
            verdict = "**整体偏宽松，风险偏好回升**，可适度承担风险。"
        elif red >= green + 2:
            verdict = "**整体偏紧缩，风险厌恶上升**，建议降低风险敞口。"
        elif red >= 2:
            verdict = "**多空交织偏紧**，关注利率与债市波动信号。"
        else:
            verdict = "**多空交织**，市场处于流动性观察期，建议保持均衡仓位。"

        lines = [
            "---",
            "",
            f"## 💧 美股资金流动性面板（{report_date}）",
            "",
            "| 指标 | 当前值 | 近 5 日变化 | 信号 | 解读 |",
            "|------|-------:|----------:|:---:|------|",
            *rows,
            "",
            f"**综合判断**：🟢{green} / 🟡{yellow} / 🔴{red} — {verdict}",
            "",
            "> *数据来源：Yahoo Finance（VIX / MOVE / 10Y / DXY / HYG），仅供研究，非投资建议。*",
        ]
        return "\n".join(lines)
