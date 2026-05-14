# -*- coding: utf-8 -*-
"""
Valuation Percentile Service
============================
拉取个股的 PE/PB/PS 历史，计算当前值在历史窗口（默认 5 年）中的百分位，
配合中文评级（极低估/偏低估/合理/偏高估/极高估），用于长期价值投资类 skill。

数据源：
- A 股：akshare.stock_zh_valuation_baidu（完整历史，~5 年日频）
- 美股：yfinance.Ticker.info 拿当前 PE/PB/PS（status='partial'，无历史分位）
- 港股：当前不支持（status='unavailable'）

可选服务，所有数据源失败时返回 status='unavailable' / 'error' 并给出说明，
不抛异常、不阻塞主流程（fail-open）。
"""

import logging
import time
from datetime import date
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL = 1800  # 30 分钟

# Metric → akshare baidu indicator 名称（A 股专用）
_AKSHARE_METRIC_MAP = {
    "pe": "市盈率(TTM)",
    "pb": "市净率",
    "ps": "市销率",
}

# Metric → yfinance.info 字段名（美股 / 国际）
_YFINANCE_METRIC_MAP = {
    "pe": "trailingPE",
    "pb": "priceToBook",
    "ps": "priceToSalesTrailing12Months",
}

# baidu period 形参映射：lookback_years → 中文
_BAIDU_PERIOD_MAP = {
    1: "近一年",
    3: "近三年",
    5: "近五年",
    10: "近十年",
}


def _detect_market(stock_code: str) -> str:
    """返回 'cn' / 'us' / 'hk'，无法识别时返回 'unknown'。"""
    c = (stock_code or "").strip().upper()
    if not c:
        return "unknown"
    # 港股
    if c.startswith("HK") or c.endswith(".HK") or (c.isdigit() and len(c) == 5):
        return "hk"
    # A 股纯数字 6 位（先于美股判断，避免数字代码被误识别）
    if c.isdigit() and len(c) == 6:
        return "cn"
    # 美股：1-5 个大写字母，可选 .X 后缀
    if c.replace(".", "").isalpha() and 1 <= len(c.replace(".", "")) <= 6:
        return "us"
    return "unknown"


def _classify_rating(percentile: float) -> Tuple[str, str, str]:
    """
    根据当前值在历史中的百分位返回评级 (emoji, label, summary)。
    百分位低 = 估值便宜（buyer's market）；百分位高 = 估值贵。
    """
    if percentile <= 10:
        return "🟢", "极低估", "处于历史极低区间，估值显著吸引力，关注布局机会"
    if percentile <= 30:
        return "🟢", "偏低估", "估值低于历史平均，长期持有性价比较高"
    if percentile < 70:
        return "⚪", "合理", "估值处于历史中位区间，无明显高低估信号"
    if percentile < 90:
        return "🔴", "偏高估", "估值高于历史平均，长期持有需谨慎"
    return "🔴", "极高估", "处于历史极高区间，警惕估值回归风险"


class ValuationPercentileService:
    """
    估值百分位服务。

    用法::

        svc = ValuationPercentileService()
        result = svc.get_valuation_data("600519", metric="pe", lookback_years=5)
        if result["status"] == "ok":
            print(result["rating"], result["current_percentile"])
    """

    def __init__(self):
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------
    def get_valuation_data(
        self,
        stock_code: str,
        metric: str = "pe",
        lookback_years: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """
        获取 stock_code 在最近 lookback_years 年内的 metric 估值历史与当前百分位。

        Returns:
            结构化 dict（见模块文档示例），所有失败状态都会返回 dict 而非抛异常。
            参数验证失败也返回 status='error' dict。
        """
        metric = (metric or "pe").lower()
        if metric not in _AKSHARE_METRIC_MAP:
            return {
                "status": "error",
                "stock_code": stock_code,
                "metric": metric,
                "note": f"unsupported metric: {metric}; supported: pe / pb / ps",
            }

        lookback_years = max(1, min(int(lookback_years or 5), 10))
        today = date.today().isoformat()
        cache_key = f"{stock_code}|{metric}|{lookback_years}|{today}"

        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        market = _detect_market(stock_code)
        if market == "cn":
            result = self._fetch_cn(stock_code, metric, lookback_years)
        elif market == "us":
            result = self._fetch_us(stock_code, metric)
        elif market == "hk":
            result = {
                "status": "unavailable",
                "stock_code": stock_code,
                "market": "hk",
                "metric": metric,
                "note": "港股估值百分位暂不支持（baidu 端点不可用），可改用基本面对比工具评估",
            }
        else:
            result = {
                "status": "error",
                "stock_code": stock_code,
                "metric": metric,
                "note": f"无法识别股票代码所属市场：{stock_code}",
            }

        if result is not None:
            self._cache[cache_key] = (now, result)
        return result

    # ------------------------------------------------------------------
    # A 股：akshare baidu
    # ------------------------------------------------------------------
    def _fetch_cn(
        self,
        stock_code: str,
        metric: str,
        lookback_years: int,
    ) -> Dict[str, Any]:
        try:
            import akshare as ak  # noqa: WPS433
        except ImportError:
            return {
                "status": "error",
                "stock_code": stock_code,
                "metric": metric,
                "note": "akshare 未安装",
            }

        indicator = _AKSHARE_METRIC_MAP[metric]
        period = _BAIDU_PERIOD_MAP.get(lookback_years, "近五年")

        try:
            df = ak.stock_zh_valuation_baidu(
                symbol=stock_code, indicator=indicator, period=period
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "valuation_percentile: A 股拉取失败 %s/%s: %s", stock_code, metric, exc
            )
            return {
                "status": "error",
                "stock_code": stock_code,
                "market": "cn",
                "metric": metric,
                "note": f"akshare.stock_zh_valuation_baidu failed: {exc}",
            }

        if df is None or len(df) == 0 or "value" not in df.columns:
            return {
                "status": "unavailable",
                "stock_code": stock_code,
                "market": "cn",
                "metric": metric,
                "note": "数据源返回空，可能股票代码不在覆盖范围",
            }

        values = df["value"].dropna().astype(float)
        if len(values) < 30:
            return {
                "status": "partial",
                "stock_code": stock_code,
                "market": "cn",
                "metric": metric,
                "current": round(float(values.iloc[-1]), 2) if len(values) else None,
                "samples": int(len(values)),
                "note": "样本数 < 30，分位数估算不可靠，仅供参考",
            }

        sorted_values = values.sort_values().reset_index(drop=True)
        current = float(values.iloc[-1])

        # 当前值在排序后的位置（含相等的部分）
        current_pos = int((sorted_values <= current).sum())
        current_percentile = round(current_pos / len(sorted_values) * 100, 1)

        emoji, label, summary = _classify_rating(current_percentile)

        percentiles = {
            "p5": round(float(values.quantile(0.05)), 2),
            "p25": round(float(values.quantile(0.25)), 2),
            "p50": round(float(values.quantile(0.50)), 2),
            "p75": round(float(values.quantile(0.75)), 2),
            "p95": round(float(values.quantile(0.95)), 2),
        }

        return {
            "status": "ok",
            "stock_code": stock_code,
            "market": "cn",
            "metric": metric,
            "indicator": indicator,
            "lookback_years": lookback_years,
            "current": round(current, 2),
            "current_percentile": current_percentile,
            "rating": label,
            "rating_emoji": emoji,
            "rating_summary": summary,
            "percentiles": percentiles,
            "history_min": round(float(values.min()), 2),
            "history_max": round(float(values.max()), 2),
            "samples": int(len(values)),
            "source": "akshare.stock_zh_valuation_baidu",
        }

    # ------------------------------------------------------------------
    # 美股：yfinance.info 当前值（无历史分位）
    # ------------------------------------------------------------------
    def _fetch_us(
        self,
        stock_code: str,
        metric: str,
    ) -> Dict[str, Any]:
        try:
            import yfinance as yf  # noqa: WPS433
        except ImportError:
            return {
                "status": "error",
                "stock_code": stock_code,
                "metric": metric,
                "note": "yfinance 未安装",
            }

        field = _YFINANCE_METRIC_MAP[metric]
        try:
            info = yf.Ticker(stock_code).info or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "valuation_percentile: 美股 info 失败 %s: %s", stock_code, exc
            )
            return {
                "status": "error",
                "stock_code": stock_code,
                "market": "us",
                "metric": metric,
                "note": f"yfinance info fetch failed: {exc}",
            }

        current = info.get(field)
        if current is None:
            return {
                "status": "unavailable",
                "stock_code": stock_code,
                "market": "us",
                "metric": metric,
                "note": (
                    f"yfinance.info 缺字段 {field}（{stock_code}），"
                    "可能为 ETF / 指数 / 数据待补"
                ),
            }

        return {
            "status": "partial",
            "stock_code": stock_code,
            "market": "us",
            "metric": metric,
            "current": round(float(current), 2),
            "current_percentile": None,
            "rating": "数据有限",
            "rating_emoji": "⚪",
            "rating_summary": (
                "美股仅取到当前值，未接入历史分位数据源。建议结合行业平均与公司"
                "5 年 PE/PB 范围（LLM 内置知识或新闻搜索）作辅助判断，不做百分位定级。"
            ),
            "samples": 1,
            "source": "yfinance.Ticker.info",
            "note": "history percentile not available; only current value returned",
        }
