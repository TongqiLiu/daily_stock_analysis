"""
估值分析器

便宜是硬道理。好公司+好行业+贵价格=慢性中毒。
核心原则：PE是结果不是原因，先看基本面有没有问题，再看PE。
"""

from dataclasses import dataclass
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ValuationAnalysis:
    """估值分析结果"""
    pe_score: float  # PE 分析 (0-25)
    supplementary_score: float  # 辅助估值 (0-25)
    historical_percentile_score: float  # 历史分位 (0-25)
    safety_margin_score: float  # 安全边际 (0-25)

    total_score: float  # 总分 (0-100)

    pe_analysis: str
    supplementary_analysis: str
    historical_analysis: str
    safety_margin_analysis: str

    is_value_trap: bool  # 是否估值陷阱
    trap_warnings: str


class ValuationAnalyzer:
    """估值分析器"""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def analyze(
        self,
        symbol: str,
        company_name: str,
        market_data: Dict,
        financial_data: Dict,
        company_score: float
    ) -> ValuationAnalysis:
        """分析估值"""

        # 1. PE 分析
        pe_score, pe_analysis = await self._analyze_pe(
            company_name, market_data, financial_data, company_score
        )

        # 2. 辅助估值指标
        supp_score, supp_analysis = self._analyze_supplementary(
            market_data, financial_data
        )

        # 3. 历史估值分位
        hist_score, hist_analysis = self._analyze_historical_percentile(
            market_data
        )

        # 4. 安全边际
        safety_score, safety_analysis = self._analyze_safety_margin(
            market_data, financial_data
        )

        total_score = pe_score + supp_score + hist_score + safety_score

        # 估值陷阱检查
        is_trap, trap_warnings = self._check_value_trap(financial_data, market_data)

        return ValuationAnalysis(
            pe_score=pe_score,
            supplementary_score=supp_score,
            historical_percentile_score=hist_score,
            safety_margin_score=safety_score,
            total_score=total_score,
            pe_analysis=pe_analysis,
            supplementary_analysis=supp_analysis,
            historical_analysis=hist_analysis,
            safety_margin_analysis=safety_analysis,
            is_value_trap=is_trap,
            trap_warnings=trap_warnings
        )

    async def _analyze_pe(
        self,
        company_name: str,
        market_data: Dict,
        financial_data: Dict,
        company_score: float
    ) -> tuple[float, str]:
        """PE 分析 - 先问为什么PE低/高"""
        pe = market_data.get('pe_ttm', 0)

        prompt = f"""
{company_name} 的 PE-TTM 为 {pe:.1f}。

请分析：
1. 这个 PE 水平是高还是低？
2. 为什么会是这个 PE？（基本面好还是差？）
3. 是否存在估值陷阱？

评分标准 (0-25)：
- PE < 10 且基本面好：20-25 分
- PE 10-20 且基本面好：15-20 分
- PE 20-30 且高增长支撑：10-15 分
- PE > 30 或基本面差：0-10 分

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=25)
        return score, analysis

    def _analyze_supplementary(
        self,
        market_data: Dict,
        financial_data: Dict
    ) -> tuple[float, str]:
        """辅助估值指标 (PB/PS/PEG)"""
        pb = market_data.get('pb', 0)
        ps = market_data.get('ps', 0)
        peg = market_data.get('peg', 0)

        score = 15.0  # 基础分

        # PB 评估
        if pb < 1.0:
            score += 3
        elif pb < 2.0:
            score += 2
        elif pb > 5.0:
            score -= 2

        # PEG 评估
        if peg and peg < 1.0:
            score += 5
        elif peg and peg > 2.0:
            score -= 3

        score = max(0, min(25, score))

        peg_str = f"{peg:.2f}" if peg else 'N/A'
        analysis = f"""
PB: {pb:.2f}
PS: {ps:.2f}
PEG: {peg_str}

综合评价: {'估值合理' if score >= 15 else '估值偏高'}
"""
        return score, analysis

    def _analyze_historical_percentile(self, market_data: Dict) -> tuple[float, str]:
        """历史估值分位"""
        percentile = market_data.get('pe_percentile_5y', 50)

        if percentile <= 20:
            score = 22.5
            comment = "极度低估"
        elif percentile <= 40:
            score = 17.5
            comment = "偏低估"
        elif percentile <= 60:
            score = 12.5
            comment = "合理"
        elif percentile <= 80:
            score = 7.5
            comment = "偏高估"
        else:
            score = 2.5
            comment = "极度高估"

        analysis = f"当前 PE 处于过去 5 年的 {percentile:.0f}% 分位，属于{comment}水平"
        return score, analysis

    def _analyze_safety_margin(
        self,
        market_data: Dict,
        financial_data: Dict
    ) -> tuple[float, str]:
        """安全边际评估"""
        current_price = market_data.get('current_price', 0)
        intrinsic_value = financial_data.get('intrinsic_value', current_price)

        if intrinsic_value > 0:
            margin = (intrinsic_value - current_price) / intrinsic_value
        else:
            margin = 0

        if margin > 0.4:
            score = 22.5
            comment = "安全边际充足"
        elif margin > 0.2:
            score = 17.5
            comment = "安全边际适中"
        elif margin > 0:
            score = 10
            comment = "安全边际较小"
        else:
            score = 2.5
            comment = "无安全边际"

        analysis = f"安全边际约 {margin:.1%}，{comment}"
        return score, analysis

    def _check_value_trap(
        self,
        financial_data: Dict,
        market_data: Dict
    ) -> tuple[bool, str]:
        """估值陷阱识别"""
        warnings = []

        # 营收连续下滑
        revenue_growth = financial_data.get('revenue_growth_3y', [])
        if revenue_growth and all(g < 0 for g in revenue_growth[-3:]):
            warnings.append("⚠️ 营收连续下滑")

        # 毛利率持续走低
        gross_margin = financial_data.get('gross_margin', 0)
        gross_margin_3y = financial_data.get('gross_margin_3y', [])
        if gross_margin_3y and gross_margin < min(gross_margin_3y):
            warnings.append("⚠️ 毛利率持续走低")

        # 应收账款暴增
        receivables = financial_data.get('receivables', 0)
        revenue = financial_data.get('revenue', 1)
        if receivables / revenue > 0.3:
            warnings.append("⚠️ 应收账款比例过高")

        is_trap = len(warnings) >= 2
        trap_str = "\n".join(warnings) if warnings else "✅ 无明显估值陷阱特征"

        return is_trap, trap_str

    def _parse_llm_response(self, response: str, max_score: float) -> tuple[float, str]:
        """解析 LLM 返回"""
        lines = response.strip().split('\n')
        score = 0.0
        analysis = ""

        for line in lines:
            if line.startswith("评分:") or line.startswith("评分："):
                try:
                    score_str = line.split(':', 1)[1].strip()
                    score = float(score_str.split()[0])
                    score = min(score, max_score)
                except:
                    pass
            elif line.startswith("分析:") or line.startswith("分析："):
                analysis = line.split(':', 1)[1].strip()
            elif analysis:
                analysis += "\n" + line

        return score, analysis
