# -*- coding: utf-8 -*-
"""
===================================
FutuFetcher - 富途 OpenD 可选数据源 (Priority 6)
===================================

数据来源：Futu OpenAPI（本地 OpenD 网关）
定位：美股/港股/A股的可选补充源，默认关闭（FUTU_ENABLED=false）

启用条件：
1. FUTU_ENABLED=true
2. 已安装 futu Python SDK（pip install futu-api）
3. 本地 OpenD 可连接（默认 127.0.0.1:11111）
"""

import logging
import os
import threading
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from .base import BaseFetcher, STANDARD_COLUMNS
from .realtime_types import UnifiedRealtimeQuote, RealtimeSource, safe_float
from .us_index_mapping import is_us_stock_code

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _to_futu_code(stock_code: str) -> Optional[str]:
    """Convert internal stock code to Futu security code format."""
    code = (stock_code or "").strip()
    if not code:
        return None

    upper = code.upper()
    if upper.startswith("US."):
        ticker = upper[3:].strip()
        return f"US.{ticker}" if ticker else None
    if upper.startswith("HK."):
        digits = upper[3:].strip()
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"HK.{digits.zfill(5)}"
        return None
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in {"US", "HK", "SH", "SZ", "BJ"}:
            if suffix == "HK":
                if base.isdigit() and 1 <= len(base) <= 5:
                    return f"HK.{base.zfill(5)}"
                return None
            if suffix == "US":
                return f"US.{base}"
            if suffix in {"SH", "SZ", "BJ"} and base.isdigit() and len(base) == 6:
                return f"{suffix}.{base}"

    if is_us_stock_code(upper):
        return f"US.{upper}"

    if upper.startswith("HK"):
        digits = upper[2:]
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return f"HK.{digits.zfill(5)}"
        return None

    if upper.isdigit() and len(upper) == 5:
        return f"HK.{upper.zfill(5)}"

    if upper.isdigit() and len(upper) == 6:
        if upper.startswith(("6", "5", "9")):
            return f"SH.{upper}"
        if upper.startswith(("0", "2", "3")):
            return f"SZ.{upper}"
        if upper.startswith(("4", "8")):
            return f"BJ.{upper}"
        if upper.startswith("92"):
            return f"BJ.{upper}"

    return None


class FutuFetcher(BaseFetcher):
    """Futu OpenD data source (optional)."""

    name = "FutuFetcher"
    priority = int(os.getenv("FUTU_PRIORITY", "6"))

    def __init__(self):
        self._ctx = None
        self._ctx_lock = threading.Lock()
        self._available: Optional[bool] = None

    def _is_available(self) -> bool:
        if self._available is not None:
            return self._available

        if not _env_bool("FUTU_ENABLED", False):
            self._available = False
            return False

        try:
            import futu  # noqa: F401

            self._available = True
            return True
        except Exception as exc:
            logger.warning("[Futu] SDK 不可用，请先安装 futu-api: %s", exc)
            self._available = False
            return False

    def is_available_for_request(self, capability: str = "") -> bool:
        return self._is_available()

    def _get_ctx(self):
        if self._ctx is not None:
            return self._ctx

        with self._ctx_lock:
            if self._ctx is not None:
                return self._ctx
            if not self._is_available():
                return None
            try:
                from futu import OpenQuoteContext

                host = (os.getenv("FUTU_OPEND_HOST") or "127.0.0.1").strip() or "127.0.0.1"
                port_raw = (os.getenv("FUTU_OPEND_PORT") or "11111").strip() or "11111"
                try:
                    port = int(port_raw)
                except ValueError:
                    logger.warning("[Futu] FUTU_OPEND_PORT=%r 非法，回退到 11111", port_raw)
                    port = 11111

                self._ctx = OpenQuoteContext(host=host, port=port)
                logger.info("[Futu] OpenQuoteContext 初始化成功: %s:%s", host, port)
                return self._ctx
            except Exception as exc:
                logger.warning("[Futu] OpenQuoteContext 初始化失败: %s", exc)
                return None

    def close(self) -> None:
        with self._ctx_lock:
            if self._ctx is None:
                return
            try:
                self._ctx.close()
            except Exception as exc:
                logger.debug("[Futu] close() 失败: %s", exc)
            finally:
                self._ctx = None

    def get_realtime_quote(self, stock_code: str) -> Optional[UnifiedRealtimeQuote]:
        if not self.is_available_for_request("realtime_quote"):
            return None

        futu_code = _to_futu_code(stock_code)
        if futu_code is None:
            return None

        ctx = self._get_ctx()
        if ctx is None:
            return None

        try:
            from futu import RET_OK

            ret, data = ctx.get_stock_quote([futu_code])
            if ret != RET_OK or data is None or data.empty:
                return None
            row = data.iloc[0]
        except Exception as exc:
            logger.debug("[Futu] get_stock_quote(%s) 失败: %s", futu_code, exc)
            return None

        price = safe_float(row.get("last_price"))
        if price is None or price <= 0:
            return None

        volume_raw = row.get("volume")
        volume_float = safe_float(volume_raw)
        volume = int(volume_float) if volume_float is not None else None

        quote = UnifiedRealtimeQuote(
            code=stock_code,
            name=str(row.get("stock_name", "") or ""),
            source=RealtimeSource.FUTU,
            price=price,
            change_pct=safe_float(row.get("change_rate")),
            change_amount=safe_float(row.get("change_val")),
            volume=volume if volume is not None and volume > 0 else None,
            amount=safe_float(row.get("turnover")),
            turnover_rate=safe_float(row.get("turnover_rate")),
            amplitude=safe_float(row.get("amplitude")),
            open_price=safe_float(row.get("open_price")),
            high=safe_float(row.get("high_price")),
            low=safe_float(row.get("low_price")),
            pre_close=safe_float(row.get("prev_close_price")),
            pe_ratio=safe_float(row.get("pe_ratio")),
            pb_ratio=safe_float(row.get("pb_ratio")),
            total_mv=safe_float(row.get("total_market_val")),
            circ_mv=safe_float(row.get("circular_market_val")),
        )

        return quote if quote.has_basic_data() else None

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        if not self.is_available_for_request("daily_data"):
            raise RuntimeError("Futu temporarily unavailable")

        futu_code = _to_futu_code(stock_code)
        if futu_code is None:
            raise ValueError(f"Cannot convert {stock_code} to Futu code")

        ctx = self._get_ctx()
        if ctx is None:
            raise RuntimeError("Futu OpenQuoteContext not available")

        try:
            from futu import RET_OK, KLType, AuType

            result = ctx.request_history_kline(
                code=futu_code,
                start=start_date,
                end=end_date,
                ktype=KLType.K_DAY,
                autype=AuType.QFQ,
            )
            if isinstance(result, tuple) and len(result) >= 2:
                ret, data = result[0], result[1]
            else:
                raise RuntimeError(f"Unexpected response: {type(result).__name__}")
            if ret != RET_OK or data is None:
                raise RuntimeError(str(data))
            if data.empty:
                return pd.DataFrame()
            return data.copy()
        except Exception as exc:
            logger.debug("[Futu] request_history_kline(%s) 失败: %s", futu_code, exc)
            raise

    def get_weekly_data(self, stock_code: str, weeks: int = 104) -> pd.DataFrame:
        """Fetch provider-native weekly OHLCV bars from Futu OpenD.

        Weekly bars are intentionally requested from OpenD instead of being
        reconstructed from the project's daily cache.  Callers can still use
        daily aggregation as a fallback when this optional provider is not
        available.
        """
        if not self.is_available_for_request("weekly_data"):
            raise RuntimeError("Futu temporarily unavailable")

        futu_code = _to_futu_code(stock_code)
        if futu_code is None:
            raise ValueError(f"Cannot convert {stock_code} to Futu code")

        try:
            weeks = max(1, min(int(weeks), 260))
        except (TypeError, ValueError):
            weeks = 104

        ctx = self._get_ctx()
        if ctx is None:
            raise RuntimeError("Futu OpenQuoteContext not available")

        try:
            from futu import RET_OK, KLType, AuType

            end_date = date.today().isoformat()
            start_date = (date.today() - timedelta(days=(weeks + 2) * 7)).isoformat()

            ret, data, *_ = ctx.request_history_kline(
                code=futu_code,
                start=start_date,
                end=end_date,
                ktype=KLType.K_WEEK,
                autype=AuType.QFQ,
            )
            if ret != RET_OK or data is None:
                raise RuntimeError(str(data))
            if data.empty:
                return pd.DataFrame()
            normalized = self._normalize_data(data.copy(), stock_code)
            return normalized.tail(weeks).reset_index(drop=True)
        except Exception as exc:
            logger.debug("[Futu] request_weekly_kline(%s) 失败: %s", futu_code, exc)
            raise

    def get_intraday_data(
        self,
        stock_code: str,
        timeframe: str = "15m",
        bars: int = 300,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> pd.DataFrame:
        """Fetch provider-native intraday OHLCV bars from Futu OpenD.

        The returned ``date`` column preserves Futu's full ``time_key`` so
        callers can isolate the latest trading session before calculating
        session VWAP.  Daily bars must never be used as a VWAP substitute.
        """
        if not self.is_available_for_request("intraday_data"):
            raise RuntimeError("Futu temporarily unavailable")

        futu_code = _to_futu_code(stock_code)
        if futu_code is None:
            raise ValueError(f"Cannot convert {stock_code} to Futu code")

        timeframe_map = {
            "1m": "K_1M",
            "3m": "K_3M",
            "5m": "K_5M",
            "15m": "K_15M",
            "30m": "K_30M",
            "1H": "K_60M",
        }
        enum_name = timeframe_map.get(timeframe)
        if enum_name is None:
            raise ValueError(
                f"Unsupported Futu intraday timeframe: {timeframe}. "
                f"Supported: {list(timeframe_map)}"
            )

        try:
            bars = max(1, min(int(bars), 1000))
        except (TypeError, ValueError):
            bars = 300

        end = end_date or date.today()
        start = start_date or (end - timedelta(days=30))
        ctx = self._get_ctx()
        if ctx is None:
            raise RuntimeError("Futu OpenQuoteContext not available")

        try:
            from futu import RET_OK, KLType, AuType

            ktype = getattr(KLType, enum_name, None)
            if ktype is None:
                raise RuntimeError(f"Installed Futu SDK does not support {enum_name}")

            frames = []
            page_req_key = None
            for _ in range(20):
                request_kwargs = {
                    "code": futu_code,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "ktype": ktype,
                    "autype": AuType.QFQ,
                    "max_count": 1000,
                }
                if page_req_key is not None:
                    request_kwargs["page_req_key"] = page_req_key

                result = ctx.request_history_kline(**request_kwargs)
                if not isinstance(result, tuple) or len(result) < 2:
                    raise RuntimeError(
                        f"Unexpected Futu response: {type(result).__name__}"
                    )
                ret, page_data = result[0], result[1]
                if ret != RET_OK or page_data is None:
                    raise RuntimeError(str(page_data))
                if not page_data.empty:
                    frames.append(page_data.copy())

                page_req_key = result[2] if len(result) >= 3 else None
                if page_req_key is None:
                    break
            else:
                raise RuntimeError("Futu intraday pagination exceeded 20 pages")

            if not frames:
                return pd.DataFrame()

            normalized = pd.concat(frames, ignore_index=True)
            if "time_key" in normalized.columns:
                normalized = normalized.rename(columns={"time_key": "date"})
            elif "date" not in normalized.columns:
                raise RuntimeError("Futu intraday response missing time_key")

            required = ["date", "open", "high", "low", "close", "volume"]
            missing = [column for column in required if column not in normalized.columns]
            if missing:
                raise RuntimeError(f"Futu intraday response missing columns: {missing}")

            for column in ("open", "high", "low", "close", "volume"):
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
            normalized = normalized.dropna(subset=required)
            normalized = normalized.drop_duplicates(subset=["date"], keep="last")
            normalized = normalized.sort_values("date").tail(bars).reset_index(drop=True)
            return normalized[required]
        except Exception as exc:
            logger.debug("[Futu] request_intraday_kline(%s) 失败: %s", futu_code, exc)
            raise

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=STANDARD_COLUMNS)

        normalized = df.copy()
        if "time_key" in normalized.columns:
            normalized["date"] = normalized["time_key"].astype(str).str.slice(0, 10)
        elif "date" not in normalized.columns:
            normalized["date"] = None

        if "turnover" in normalized.columns and "amount" not in normalized.columns:
            normalized = normalized.rename(columns={"turnover": "amount"})

        if "change_rate" in normalized.columns and "pct_chg" not in normalized.columns:
            normalized = normalized.rename(columns={"change_rate": "pct_chg"})

        if "pct_chg" not in normalized.columns and "close" in normalized.columns:
            normalized["pct_chg"] = normalized["close"].pct_change() * 100

        for col in STANDARD_COLUMNS:
            if col not in normalized.columns:
                normalized[col] = None

        # 对齐类型，减少后续指标链路中的字符串数值干扰
        for col in ("open", "high", "low", "close", "amount", "pct_chg"):
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
        normalized["volume"] = pd.to_numeric(normalized["volume"], errors="coerce").fillna(0).astype(int)

        normalized["date"] = normalized["date"].astype(str).str.slice(0, 10)
        return normalized[STANDARD_COLUMNS]
