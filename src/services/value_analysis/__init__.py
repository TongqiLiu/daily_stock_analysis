"""
价值投资分析服务模块

基于邱国鹭《投资中最简单的事》的投资方法论，
对股票进行系统化的价值投资评估。

核心原则：
- 投资价值 = 好行业 × 好公司 × 好价格
- 三个维度缺一不可
"""

from .data_validator import DataValidator
from .industry_analyzer import IndustryAnalyzer
from .company_analyzer import CompanyAnalyzer, MoatTripleTest
from .valuation_analyzer import ValuationAnalyzer
from .contrarian_checker import ContrarianChecker
from .pricing_power_analyzer import PricingPowerAnalyzer
from .value_report_generator import ValueReportGenerator

__all__ = [
    "DataValidator",
    "IndustryAnalyzer",
    "CompanyAnalyzer",
    "MoatTripleTest",
    "ValuationAnalyzer",
    "ContrarianChecker",
    "PricingPowerAnalyzer",
    "ValueReportGenerator",
]
