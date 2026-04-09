# -*- coding: utf-8 -*-
"""
Druckenmiller Conviction Service
=================================
从 druckenmiller-skills.vercel.app 获取每日 conviction JSON，
格式化为大盘复盘报告中可附加的 Markdown 段落。

可选服务，无需认证。数据源不可用时静默跳过，不影响主流程。
"""

import logging
import time
from datetime import date, timedelta
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://druckenmiller-skills.vercel.app"
_REQUEST_TIMEOUT = 8
_CACHE_TTL = 1800  # 30 分钟

# Zone → 中文说明
_ZONE_ZH = {
    "fat pitch": "极强信号（全力出手）",
    "high conviction": "高确信度（积极加码）",
    "moderate": "中等确信度（标准仓位）",
    "low conviction": "低确信度（缩仓观望）",
    "capital preservation": "资本保全（最大防守）",
}

# Zone → 仓位颜色 emoji
_ZONE_EMOJI = {
    "fat pitch": "🟢",
    "high conviction": "🟢",
    "moderate": "🟡",
    "low conviction": "🟠",
    "capital preservation": "🔴",
}

_DIRECTION_ZH = {
    "expanding": "扩张↑",
    "pivot": "转向↑",
    "tightening": "收紧↓",
    "neutral": "中性→",
    "beat": "超预期↑",
    "miss": "低于预期↓",
    "healthy": "健康↑",
    "deteriorating": "恶化↓",
}


class DruckenmillerConvictionService:
    """
    Druckenmiller Conviction Service。

    获取每日 conviction JSON，格式化为可附加到大盘复盘报告的 Markdown 块。

    用法::

        svc = DruckenmillerConvictionService()
        block = svc.get_conviction_block()
        if block:
            report += "\\n\\n" + block
    """

    def __init__(self, base_url: str = _DEFAULT_BASE_URL):
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._cache: Dict[str, tuple] = {}  # date_str → (timestamp, data)

    def _fetch(self, date_str: str) -> Optional[Dict]:
        """拉取指定日期 conviction JSON，返回解析后的 dict 或 None。"""
        url = f"{self._base_url}/reports/conviction_{date_str}.json"
        try:
            resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("Druckenmiller conviction %s: HTTP %s", date_str, resp.status_code)
        except requests.exceptions.Timeout:
            logger.warning("Druckenmiller conviction API 请求超时 (date=%s)", date_str)
        except Exception as e:
            logger.warning("Druckenmiller conviction API 异常 (date=%s): %s", date_str, e)
        return None

    def _fetch_cached(self, date_str: str) -> Optional[Dict]:
        """带 TTL 缓存的获取，同日期 30 分钟内复用。"""
        now = time.monotonic()
        cached = self._cache.get(date_str)
        if cached and (now - cached[0]) < _CACHE_TTL:
            return cached[1]

        data = self._fetch(date_str)
        if data is not None:
            self._cache[date_str] = (time.monotonic(), data)
        return data

    def get_conviction_data(self) -> Optional[Dict]:
        """
        获取最新 conviction 数据。
        优先今日，若不存在则 fallback 昨日。返回原始 dict 或 None。
        """
        today = date.today().isoformat()
        data = self._fetch_cached(today)
        if data is not None:
            return data

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        data = self._fetch_cached(yesterday)
        if data is not None:
            logger.info("Druckenmiller conviction: 今日数据未就绪，使用昨日数据 (%s)", yesterday)
            return data

        logger.info("Druckenmiller conviction: 暂无可用数据（今日/昨日均未生成）")
        return None

    def get_conviction_block(self) -> Optional[str]:
        """
        返回格式化的 Markdown 块，可直接附加到复盘报告。
        数据不可用时返回 None。
        """
        data = self.get_conviction_data()
        if data is None:
            return None
        return self._format(data)

    @staticmethod
    def _format(data: Dict) -> str:
        score = data.get("conviction_score", "—")
        zone = data.get("conviction_zone", "—")
        equity_range = data.get("equity_range", "—")
        action = data.get("action", "—")
        narrative = data.get("narrative", "")
        druck_quote = data.get("druck_quote", "")
        blow_off = data.get("blow_off_risk", False)
        divergences = data.get("notable_divergences", [])
        components = data.get("components", {})
        report_date = data.get("date", "")

        zone_zh = _ZONE_ZH.get(zone, zone)
        zone_emoji = _ZONE_EMOJI.get(zone, "⚪")
        date_hint = f"（{report_date}）" if report_date else ""

        lines = [
            f"---",
            f"",
            f"## 📊 Druckenmiller Conviction{date_hint}",
            f"",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 确信度评分 | **{score}/100** |",
            f"| 信号区间 | {zone_emoji} {zone_zh} |",
            f"| 建议权益仓位 | {equity_range} |",
            f"| 操作建议 | {action} |",
        ]

        if blow_off:
            lines.append(f"| ⚠️ 过热风险 | 仓位上限强制降至 50% |")

        # 4 分项信号
        if components:
            lines += ["", "**分项信号：**", ""]
            comp_label = {
                "liquidity-regime": "流动性（35%）",
                "forward-earnings": "盈利预期（25%）",
                "market-breadth": "市场广度（25%）",
                "price-signal": "价格信号（15%）",
            }
            for key, label in comp_label.items():
                comp = components.get(key, {})
                direction = comp.get("direction", "—")
                direction_zh = _DIRECTION_ZH.get(direction, direction)
                comp_score = comp.get("score", "—")
                stale = " ⚠️过期" if comp.get("stale") else ""
                lines.append(f"- {label}：{comp_score:.1f}分，{direction_zh}{stale}")

        # 叙事说明
        if narrative:
            lines += ["", f"> {narrative}"]

        # Divergence 警告
        if divergences:
            lines += ["", "⚠️ **盈好价跌警告（6个月预警信号）：**"]
            for ticker in divergences:
                lines.append(f"- {ticker}")

        # 引言
        if druck_quote:
            lines += ["", f'> *"{druck_quote}"*', "> — Stanley Druckenmiller"]

        lines += ["", "*数据来源：Yahoo Finance / FRED / FMP，仅供研究，非投资建议。*"]
        return "\n".join(lines)
