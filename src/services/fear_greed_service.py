# -*- coding: utf-8 -*-
"""
贪恐指数服务
============
通过 szdt.tech API 查询个股贪恐指数（约 -100~100 分），并格式化为 LLM 提示词可直接注入的文本块。

可选服务，需配置 SZDT_AUTH_TOKEN，缺失时静默跳过。
支持 A 股（SH/SZ）、港股（HK）、美股（US）。
"""

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://szdt.tech"
_SCAN_PATH = "/api/partner/invest/stock/scan"
_REQUEST_TIMEOUT = 8
_CACHE_TTL = 1800  # 30 分钟；贪恐值变化慢，可较长缓存


def _score_label(score: float) -> str:
    """将贪恐分转换为可读标签（实测范围约 -100~100）。"""
    if score <= -60:
        return "极度恐慌"
    if score <= -20:
        return "恐慌"
    if score < 20:
        return "中性"
    if score < 60:
        return "贪婪"
    return "极度贪婪"


def to_szdt_code(code: str) -> Optional[Tuple[str, str]]:
    """
    将系统内部股票代码转换为 szdt.tech 所需格式。

    返回 (szdt_code, emo_area) 或 None（无法识别时）。

    映射规则：
    - A 股 6 位（SH/SZ）→ (SH.XXXXXX / SZ.XXXXXX, "a")
    - 港股 HK00700 → (HK.00700, "a")
    - 美股 AMZN → (US.AMZN, "us")
    """
    c = (code or "").strip().upper()

    # 港股：HK00700 → HK.00700
    if c.startswith("HK") and c[2:].isdigit():
        return f"HK.{c[2:]}", "a"

    # A 股纯数字 6 位
    # SH：6xxxxx（主板）/ 5xxxxx（ETF/LOF）/ 9xxxxx（B股）/ 11xxxx（可转债）/ 12xxxx
    # SZ：0xxxxx（主板）/ 3xxxxx（创业板/中小板）/ 15xxxx-16xxxx（ETF）
    if c.isdigit() and len(c) == 6:
        if c[0] in ("6", "5", "9") or c[:2] in ("11", "12"):
            return f"SH.{c}", "a"
        return f"SZ.{c}", "a"

    # 美股：纯字母 ticker（1~5 字符）
    if c.isalpha() and 1 <= len(c) <= 5:
        return f"US.{c}", "us"

    # 美股 ADR/特殊 ticker（字母+数字，不含 .）
    if c.replace(".", "").isalnum() and "." not in c and len(c) <= 10:
        if any(ch.isalpha() for ch in c):
            return f"US.{c}", "us"

    return None


class FearGreedService:
    """
    贪恐指数服务。

    查询 szdt.tech 个股贪恐指数，格式化为 LLM 提示词文本块。

    用法::

        svc = FearGreedService(auth_token="o3koB1le...")
        if svc.is_available:
            context = svc.get_fear_greed_context("TSLA")
    """

    def __init__(self, auth_token: Optional[str] = None):
        self._token = (auth_token or "").strip() or None
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.RLock()

    @property
    def is_available(self) -> bool:
        return self._token is not None

    # ------------------------------------------------------------------
    # 内部请求
    # ------------------------------------------------------------------

    def _post(self, szdt_code: str, emo_area: str, lever: str = "1") -> Optional[Dict]:
        """发起 form-data POST 请求，返回 data 字段或 None。"""
        url = _BASE_URL + _SCAN_PATH
        headers = {"X-Auth": self._token}
        data = {"code": szdt_code, "lever": lever, "emo_area": emo_area}
        try:
            resp = requests.post(url, headers=headers, data=data, timeout=_REQUEST_TIMEOUT)
            if resp.status_code == 200:
                body = resp.json()
                if body.get("status") == 1:
                    return body.get("data")
                logger.warning("贪恐 API 返回错误：%s / code=%s", body.get("msg"), szdt_code)
            else:
                logger.warning("贪恐 API HTTP %s / code=%s", resp.status_code, szdt_code)
        except requests.exceptions.Timeout:
            logger.warning("贪恐 API 请求超时 / code=%s", szdt_code)
        except Exception as e:
            logger.warning("贪恐 API 异常 / code=%s: %s", szdt_code, e)
        return None

    def _fetch_cached(self, szdt_code: str, emo_area: str) -> Optional[Dict]:
        """带 TTL 缓存的请求，同 code 30 分钟内复用。"""
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(szdt_code)
            if cached and (now - cached[0]) < _CACHE_TTL:
                return cached[1]

        data = self._post(szdt_code, emo_area)
        if data is not None:
            with self._lock:
                self._cache[szdt_code] = (time.monotonic(), data)
        return data

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_score(self, code: str) -> Optional[Tuple[float, str]]:
        """
        查询贪恐指数，返回 (score, label) 或 None。

        score 范围约 -100~100；label 为可读中文标签（极度恐慌/恐慌/中性/贪婪/极度贪婪）。
        结果与 get_fear_greed_context 共享缓存，不产生额外 API 调用。
        """
        if not self.is_available:
            return None

        mapping = to_szdt_code(code)
        if mapping is None:
            return None

        szdt_code, emo_area = mapping
        data = self._fetch_cached(szdt_code, emo_area)
        if data is None:
            return None

        score = data.get("score")
        if score is None:
            return None

        score_f = float(score)
        return score_f, _score_label(score_f)

    def get_fear_greed_context(self, code: str) -> Optional[str]:
        """
        查询贪恐指数，返回 LLM 可注入的文本块；数据不可用时返回 None。
        """
        if not self.is_available:
            return None

        mapping = to_szdt_code(code)
        if mapping is None:
            logger.debug("贪恐指数：无法识别股票代码格式 %s，跳过", code)
            return None

        szdt_code, emo_area = mapping
        data = self._fetch_cached(szdt_code, emo_area)
        if data is None:
            return None

        return self._format(code, data)

    # ------------------------------------------------------------------
    # 格式化
    # ------------------------------------------------------------------

    @staticmethod
    def _format(code: str, data: Dict) -> str:
        score = data.get("score")
        name = data.get("name", code)
        price = data.get("price")
        ts = data.get("time", "")

        lines = [f"📊 贪恐指数（szdt.tech）— {name}（{code.upper()}）"]
        lines.append("=" * 50)

        if score is not None:
            label = _score_label(float(score))
            lines.append(f"  贪恐分：{score}  →  {label}")
        else:
            lines.append("  贪恐分：暂无数据")

        if price is not None:
            lines.append(f"  当前价：{price}")
        if ts:
            lines.append(f"  更新时间（北京时间）：{ts}")

        lines.append("")
        lines.append(
            "说明：贪恐指数范围约 -100~100，负值偏恐慌，正值偏贪婪，"
            "可辅助判断市场情绪极值，不作为单一买卖信号。"
        )
        return "\n".join(lines)
