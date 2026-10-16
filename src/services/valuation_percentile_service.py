# -*- coding: utf-8 -*-
"""
Valuation Percentile Service
============================
拉取个股的 PE/PB/PS 历史，计算当前值在历史窗口（默认 5 年）中的百分位，
配合历史区间描述（历史极低/历史偏低/历史中位/历史偏高/历史极高），用于长期价值投资类 skill。

数据源：
- A 股：akshare.stock_zh_valuation_baidu（完整历史，~5 年日频）
- 美股：yfinance.Ticker.info 拿当前 PE/PB/PS；PE/PB 在 info 失败时尝试实时行情降级
  （status='partial'，无历史分位）
- 港股：当前不支持（status='unavailable'）

可选服务，所有数据源失败时返回 status='unavailable' / 'error' 并给出说明，
不抛异常、不阻塞主流程（fail-open）。
"""

import logging
import math
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Optional, Tuple

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
    只描述相对历史位置，不代表内在价值或买卖信号。
    """
    if percentile <= 10:
        return "🟢", "历史极低", "倍数处于自身历史极低区间，不代表内在低估；需核对盈利质量和结构变化"
    if percentile <= 30:
        return "🟢", "历史偏低", "倍数低于多数历史观测，不等于具备安全边际"
    if percentile < 70:
        return "⚪", "历史中位", "倍数处于历史中位区间，不能据此判断合理价值"
    if percentile < 90:
        return "🔴", "历史偏高", "倍数高于多数历史观测，需核对增长、利率和业务结构变化"
    return "🔴", "历史极高", "倍数处于自身历史极高区间，不是独立减仓信号"


class ValuationPercentileService:
    """
    估值百分位服务。

    用法::

        svc = ValuationPercentileService()
        result = svc.get_valuation_data("600519", metric="pe", lookback_years=5)
        if result["status"] == "ok":
            print(result["rating"], result["current_percentile"])
    """

    def __init__(self, realtime_quote_fetcher: Optional[Callable[[str], Any]] = None):
        self._cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._realtime_quote_fetcher = realtime_quote_fetcher

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

        try:
            lookback_years = int(lookback_years)
        except (TypeError, ValueError, OverflowError):
            lookback_years = 0
        if lookback_years not in _BAIDU_PERIOD_MAP:
            return {"status": "error", "stock_code": stock_code, "metric": metric,
                    "note": "lookback_years must be one of 1, 3, 5, 10"}
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
            result.setdefault("retrieved_at", datetime.now(timezone.utc).isoformat())
            result.setdefault("as_of", None)
            result["interpretation"] = "relative_history_only_not_intrinsic_value"
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

        import pandas as pd

        base = {
            "stock_code": stock_code, "market": "cn", "metric": metric,
            "source": "akshare.stock_zh_valuation_baidu", "lookback_years": lookback_years,
            "current_percentile": None,
        }
        if "date" not in df.columns:
            return {**base, "status": "partial", "current": None, "samples": 0,
                    "note": "缺少历史观测日期，无法确认最新值和回看窗口"}
        frame = df[["date", "value"]].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True)
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        today = pd.Timestamp(datetime.now(timezone.utc).date(), tz="UTC")
        start_date = today - pd.DateOffset(years=lookback_years)
        frame = frame.loc[frame["date"].between(start_date, today)].sort_values("date")
        if frame.empty:
            return {**base, "status": "unavailable", "current": None, "samples": 0,
                    "note": "所请求窗口没有带有效日期的历史数据"}
        if (frame.groupby("date")["value"].nunique(dropna=False) > 1).any():
            return {**base, "status": "partial", "current": None, "samples": 0,
                    "note": "同一天有冲突估值，不能把重复或冲突记录当成独立样本"}
        frame = frame.drop_duplicates("date", keep="last")
        latest = frame.iloc[-1]
        current = float(latest["value"])
        usable = frame["value"].map(lambda value: math.isfinite(value) and value > 0)
        values = frame.loc[usable, "value"]
        base.update({
            "as_of": latest["date"].date().isoformat(),
            "history_start": frame.iloc[0]["date"].date().isoformat(),
            "history_end": latest["date"].date().isoformat(),
            "history_coverage_years": round((latest["date"] - frame.iloc[0]["date"]).days / 365.25, 2),
            "samples": int(len(values)), "excluded_nonpositive_or_invalid": int((~usable).sum()),
        })
        if not math.isfinite(current) or current <= 0:
            return {**base, "status": "partial", "current": None,
                    "note": "最新估值非正或无效（亏损/负净资产等），不能用更早的正值替代当前值"}
        if len(values) < 30 or (today - latest["date"]).days > 31:
            return {**base, "status": "partial", "current": round(current, 2),
                    "note": "有效样本不足 30 或最新历史观测已超过 31 天，不提供当前分位"}

        sorted_values = values.sort_values().reset_index(drop=True)

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
            **base,
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
        field = _YFINANCE_METRIC_MAP[metric]
        try:
            import yfinance as yf  # noqa: WPS433
        except ImportError:
            return self._fetch_us_realtime_quote_metric(
                stock_code,
                metric,
                upstream_note="yfinance 未安装",
                upstream_status="error",
            )

        try:
            info = yf.Ticker(stock_code).info or {}
        except Exception as exc:  # noqa: BLE001
            result = self._fetch_us_realtime_quote_metric(
                stock_code,
                metric,
                upstream_note=f"yfinance info fetch failed: {exc}",
                upstream_status="error",
            )
            if result.get("status") in {"partial", "unavailable"}:
                logger.info(
                    "valuation_percentile: 美股 info 失败 %s/%s，已按实时行情估值降级处理: %s",
                    stock_code,
                    metric,
                    exc,
                )
            else:
                logger.warning(
                    "valuation_percentile: 美股 info 失败 %s/%s，实时行情降级也不可用: %s",
                    stock_code,
                    metric,
                    exc,
                )
            return result

        current = info.get(field)
        if current is None:
            return self._fetch_us_realtime_quote_metric(
                stock_code,
                metric,
                upstream_note=(
                    f"yfinance.info 缺字段 {field}（{stock_code}），"
                    "可能为 ETF / 指数 / 数据待补"
                ),
                upstream_status="unavailable",
            )

        return self._build_us_partial_result(
            stock_code=stock_code,
            metric=metric,
            current=current,
            source="yfinance.Ticker.info",
            note="history percentile not available; only current value returned",
        )

    def _build_us_partial_result(
        self,
        *,
        stock_code: str,
        metric: str,
        current: Any,
        source: str,
        note: str,
        fallback_from: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            number = float(current)
        except (ValueError, TypeError, OverflowError):
            number = float("nan")
        if not math.isfinite(number) or number <= 0 or isinstance(current, bool):
            return {"status": "unavailable", "stock_code": stock_code, "market": "us",
                    "metric": metric, "source": source, "current": None,
                    "current_percentile": None, "note": "当前估值非正或无效，不支持倍数分位判断"}
        return {
            "status": "partial",
            "stock_code": stock_code,
            "market": "us",
            "metric": metric,
            "current": round(number, 2),
            "current_percentile": None,
            "rating": "数据有限",
            "rating_emoji": "⚪",
            "rating_summary": (
                "美股仅取到当前值，未接入历史分位数据源；禁止用模型记忆补历史或行业均值。"
                "如需比较，须另取带来源、期间和可比口径的证据，不做百分位定级。"
            ),
            "samples": 1,
            "source": source,
            "note": note,
            **({"fallback_from": fallback_from} if fallback_from else {}),
        }

    def _fetch_us_realtime_quote_metric(
        self,
        stock_code: str,
        metric: str,
        *,
        upstream_note: str,
        upstream_status: str,
    ) -> Dict[str, Any]:
        """Use realtime quote PE/PB as a best-effort fallback for US valuation."""
        realtime_field = {
            "pe": "pe_ratio",
            "pb": "pb_ratio",
        }.get(metric)
        if realtime_field is None:
            return {
                "status": upstream_status,
                "stock_code": stock_code,
                "market": "us",
                "metric": metric,
                "note": f"{upstream_note}; realtime quote fallback does not support {metric}",
            }

        try:
            quote = self._get_realtime_quote(stock_code)
        except Exception as exc:  # noqa: BLE001
            return {
                "status": upstream_status,
                "stock_code": stock_code,
                "market": "us",
                "metric": metric,
                "note": f"{upstream_note}; realtime quote fallback failed: {exc}",
            }

        current = getattr(quote, realtime_field, None) if quote is not None else None
        try:
            current_value = float(current)
        except (TypeError, ValueError):
            current_value = 0.0

        if current_value <= 0:
            status = "unavailable" if quote is not None else upstream_status
            return {
                "status": status,
                "stock_code": stock_code,
                "market": "us",
                "metric": metric,
                "note": (
                    f"{upstream_note}; realtime quote fallback missing {realtime_field}"
                ),
            }

        source = getattr(quote, "source", None)
        if hasattr(source, "value"):
            source_name = f"realtime_quote.{source.value}"
        elif source:
            source_name = f"realtime_quote.{source}"
        else:
            source_name = "realtime_quote"

        return self._build_us_partial_result(
            stock_code=stock_code,
            metric=metric,
            current=current_value,
            source=source_name,
            note=(
                "history percentile not available; only current value returned "
                f"from realtime quote fallback. upstream: {upstream_note}"
            ),
            fallback_from="yfinance.Ticker.info",
        )

    def _get_realtime_quote(self, stock_code: str) -> Any:
        if self._realtime_quote_fetcher is not None:
            return self._realtime_quote_fetcher(stock_code)

        from data_provider.base import DataFetcherManager  # noqa: WPS433

        return DataFetcherManager().get_realtime_quote(
            stock_code,
            log_final_failure=False,
        )
