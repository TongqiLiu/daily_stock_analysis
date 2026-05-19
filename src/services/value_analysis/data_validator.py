"""
多信源数据校验模块

核心原则：一切分析必须建立在可信数据之上。单一信源 = 错误风险。

信源优先级：
- A 级：公司官方公告、年报、交易所披露
- B 级：理杏仁、Wind、雪球、同花顺、富途、Bloomberg
- C 级：券商研报、财经媒体
- D 级：社交媒体、股吧（仅作情绪参考）
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class SourceTier(Enum):
    """信源等级"""
    TIER_0_API = "🟢🟢🟢🟢"  # API 直取（yfinance/AkShare/富途 OpenD）
    TIER_1_OFFICIAL = "🟢🟢🟢"  # 一级官方（公司公告/交易所/政府）
    TIER_2_AUTHORITY = "🟢🟢"  # 二级权威（Bloomberg/财新/专业数据库）
    TIER_3_REFERENCE = "🟢"  # 三级参考（36氪/搜狐/东财）
    TIER_4_WEAK = "🟡"  # 单源/弱信源
    TIER_5_RUMOR = "🔴"  # 传闻


class DataType(Enum):
    """数据类型（防止混淆）"""
    CURRENT_PRICE = "实时收盘价"
    TARGET_PRICE = "研报目标价"
    ANALYST_CONSENSUS = "分析师一致预期"
    WEEK_52_HIGH_LOW = "52周高低"
    HISTORICAL_PRICE = "历史价格"
    FAIR_VALUE = "公允价值/DCF估值"


@dataclass
class DataSource:
    """数据源记录"""
    name: str  # 信源名称
    value: float  # 数据值
    tier: SourceTier  # 信源等级
    data_type: DataType  # 数据类型
    timestamp: datetime  # 取数时间
    currency: str = "CNY"  # 币种
    note: str = ""  # 备注


@dataclass
class ValidationResult:
    """校验结果"""
    field_name: str  # 字段名
    final_value: float  # 最终采用值
    sources: List[DataSource]  # 所有信源
    is_valid: bool  # 是否通过校验
    discrepancy: float  # 差异百分比
    warning: Optional[str] = None  # 警告信息


class DataValidator:
    """数据校验器"""

    # 必须校验的关键字段
    REQUIRED_FIELDS = [
        "current_price",  # 当前股价
        "market_cap",  # 市值
        "pe_ttm",  # PE-TTM
        "revenue",  # 最新营收
        "net_profit",  # 归母净利润
        "gross_margin",  # 毛利率
        "roe",  # ROE
        "dividend_yield",  # 股息率
        "debt_ratio",  # 资产负债率
        "market_share",  # 市场份额
    ]

    def __init__(self, data_providers: Dict):
        """
        初始化数据校验器

        Args:
            data_providers: 数据提供者字典，如 {
                'longbridge': longbridge_fetcher,
                'yfinance': yfinance_fetcher,
                'akshare': akshare_fetcher,
                'futu': futu_fetcher,
            }
        """
        self.data_providers = data_providers
        self.validation_results: Dict[str, ValidationResult] = {}

    def validate_price(
        self,
        symbol: str,
        min_sources: int = 3
    ) -> ValidationResult:
        """
        校验当前股价（核心字段，至少3个独立权威源）

        Args:
            symbol: 股票代码
            min_sources: 最低信源数（价格类数据默认3）

        Returns:
            ValidationResult: 校验结果
        """
        sources: List[DataSource] = []

        # 从各个数据源获取价格
        for provider_name, provider in self.data_providers.items():
            try:
                price_data = provider.get_realtime_quote(symbol)
                if price_data and price_data.get('current'):
                    sources.append(DataSource(
                        name=provider_name,
                        value=float(price_data['current']),
                        tier=self._get_provider_tier(provider_name),
                        data_type=DataType.CURRENT_PRICE,
                        timestamp=datetime.now(),
                        currency=price_data.get('currency', 'CNY'),
                        note=f"{provider_name} 实时行情"
                    ))
            except Exception as e:
                logger.warning(f"从 {provider_name} 获取价格失败: {e}")

        # 校验逻辑
        return self._validate_field(
            field_name="current_price",
            sources=sources,
            min_sources=min_sources,
            tolerance=0.02  # 价格容忍度 2%
        )

    def validate_field(
        self,
        field_name: str,
        symbol: str,
        min_sources: int = 2,
        tolerance: float = 0.05
    ) -> ValidationResult:
        """
        校验通用字段

        Args:
            field_name: 字段名
            symbol: 股票代码
            min_sources: 最低信源数
            tolerance: 容忍度（默认5%）

        Returns:
            ValidationResult: 校验结果
        """
        sources: List[DataSource] = []

        # 从各个数据源获取数据
        for provider_name, provider in self.data_providers.items():
            try:
                # 根据字段名调用对应方法
                value = self._fetch_field_value(
                    provider, provider_name, symbol, field_name
                )
                if value is not None:
                    sources.append(DataSource(
                        name=provider_name,
                        value=float(value),
                        tier=self._get_provider_tier(provider_name),
                        data_type=DataType.CURRENT_PRICE,  # 根据字段调整
                        timestamp=datetime.now(),
                        note=f"{provider_name} 数据"
                    ))
            except Exception as e:
                logger.warning(f"从 {provider_name} 获取 {field_name} 失败: {e}")

        return self._validate_field(
            field_name=field_name,
            sources=sources,
            min_sources=min_sources,
            tolerance=tolerance
        )

    def _validate_field(
        self,
        field_name: str,
        sources: List[DataSource],
        min_sources: int,
        tolerance: float
    ) -> ValidationResult:
        """
        执行字段校验逻辑

        Args:
            field_name: 字段名
            sources: 数据源列表
            min_sources: 最低信源数
            tolerance: 容忍度

        Returns:
            ValidationResult: 校验结果
        """
        # 检查信源数量
        if len(sources) < min_sources:
            return ValidationResult(
                field_name=field_name,
                final_value=0.0,
                sources=sources,
                is_valid=False,
                discrepancy=0.0,
                warning=f"信源不足：需要至少 {min_sources} 个，实际 {len(sources)} 个"
            )

        # 按信源等级排序（优先级高的在前）
        sources.sort(key=lambda x: list(SourceTier).index(x.tier))

        # 计算差异
        values = [s.value for s in sources]
        mean_value = sum(values) / len(values)
        max_value = max(values)
        min_value = min(values)
        discrepancy = (max_value - min_value) / mean_value if mean_value > 0 else 0

        # 判断是否通过
        is_valid = discrepancy <= tolerance
        warning = None

        if discrepancy > tolerance:
            if discrepancy <= 0.15:
                # 差异 5-15%，引入第三方或以 A 级为准
                warning = f"信源间差异 {discrepancy:.1%}，已取均值但需注意"
            else:
                # 差异 > 15%，必须追溯至公司公告原文
                warning = f"信源间差异过大 {discrepancy:.1%}，数据存疑"
                is_valid = False

        # 优先采用最高等级信源的值，如果差异小则取均值
        final_value = sources[0].value if discrepancy > 0.10 else mean_value

        result = ValidationResult(
            field_name=field_name,
            final_value=final_value,
            sources=sources,
            is_valid=is_valid,
            discrepancy=discrepancy,
            warning=warning
        )

        self.validation_results[field_name] = result
        return result

    def _fetch_field_value(
        self,
        provider,
        provider_name: str,
        symbol: str,
        field_name: str
    ) -> Optional[float]:
        """从数据提供者获取字段值"""
        # 根据字段名调用不同方法
        field_mapping = {
            "market_cap": lambda: provider.get_realtime_quote(symbol).get('market_cap'),
            "pe_ttm": lambda: provider.get_realtime_quote(symbol).get('pe_ttm'),
            "revenue": lambda: provider.get_financial_data(symbol).get('revenue'),
            "net_profit": lambda: provider.get_financial_data(symbol).get('net_profit'),
            # 更多字段映射...
        }

        fetch_func = field_mapping.get(field_name)
        if fetch_func:
            try:
                return fetch_func()
            except:
                return None
        return None

    def _get_provider_tier(self, provider_name: str) -> SourceTier:
        """获取数据提供者的信源等级"""
        tier_mapping = {
            "yfinance": SourceTier.TIER_0_API,
            "akshare": SourceTier.TIER_0_API,
            "futu": SourceTier.TIER_0_API,
            "longbridge": SourceTier.TIER_2_AUTHORITY,
            "tushare": SourceTier.TIER_2_AUTHORITY,
            "efinance": SourceTier.TIER_3_REFERENCE,
        }
        return tier_mapping.get(provider_name.lower(), SourceTier.TIER_3_REFERENCE)

    def generate_validation_report(self) -> str:
        """生成数据校验记录表（Markdown格式）"""
        if not self.validation_results:
            return "## 附录：数据校验记录\n\n无校验记录"

        lines = [
            "## 附录：数据校验记录",
            "",
            "| 字段 | 数据类型 | 采用值 | 信源 1 | 信源 2 | 信源 3 | 信源等级 | 取数时间 | 差异说明 |",
            "|------|---------|--------|--------|--------|--------|:---:|---------|---------|"
        ]

        for field, result in self.validation_results.items():
            sources_str = " | ".join([
                f"{s.name} {s.value:.2f}" for s in result.sources[:3]
            ])
            tiers_str = " + ".join([s.tier.value for s in result.sources[:3]])
            timestamp_str = result.sources[0].timestamp.strftime("%Y-%m-%d %H:%M")

            status = "✅" if result.is_valid else "⚠️"
            diff_str = f"差异 {result.discrepancy:.1%} {status}"
            if result.warning:
                diff_str += f" {result.warning}"

            lines.append(
                f"| {field} | {result.sources[0].data_type.value} | "
                f"{result.final_value:.2f} | {sources_str} | "
                f"{tiers_str} | {timestamp_str} | {diff_str} |"
            )

        return "\n".join(lines)

    def validate_all_required_fields(self, symbol: str) -> Dict[str, ValidationResult]:
        """校验所有必需字段"""
        results = {}

        # 价格字段（需要3个源）
        results["current_price"] = self.validate_price(symbol, min_sources=3)

        # 其他字段（需要2个源）
        for field in self.REQUIRED_FIELDS[1:]:
            results[field] = self.validate_field(field, symbol, min_sources=2)

        return results
