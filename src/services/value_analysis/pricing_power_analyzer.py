"""
定价权分析器

一家公司值不值钱,归根结底看它有没有定价权。
核心问题：这公司能不能每年默默提个价，客户还没脾气？
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class PricingPowerLevel(Enum):
    """定价权水平"""
    STRONG = "强定价权"
    MODERATE = "中等定价权"
    WEAK = "弱定价权"
    NONE = "无定价权"


@dataclass
class PricingPowerAnalysis:
    """定价权分析结果"""
    level: PricingPowerLevel
    adjustment: float  # 总分调整 (±10%)
    sources: list  # 定价权来源
    analysis: str


class PricingPowerAnalyzer:
    """定价权分析器"""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def analyze(
        self,
        symbol: str,
        company_name: str,
        industry_name: str,
        market_data: Dict,
        financial_data: Dict
    ) -> PricingPowerAnalysis:
        """分析定价权"""

        prompt = f"""
请分析 {company_name} 在 {industry_name} 行业的定价权：

核心问题：这公司能不能每年默默提个价，客户还没脾气？

1. 定价权水平（选一个）：
   - 强定价权：可以主动提价，客户依然买单
   - 中等定价权：可以跟随通胀提价
   - 弱定价权：提价会流失客户
   - 无定价权：价格完全由外部决定

2. 定价权来源（可多选）：
   - 品牌忠诚度（消费者愿意为品牌多付钱）
   - 产品差异化（没有完全替代品）
   - 客户转换成本高（换供应商太麻烦）
   - 行业供给受限（没有那么多竞争者）
   - 刚需属性强（不买不行）

3. 护城河三重检验：
   - 底层规律：优势是否建立在人性、物理规律或经济规律上？
   - 结构：是否由多个优势互相咬合，而不是只有一个卖点？
   - 飞轮：优势是否能越转越快、自我强化？

只有三关全过，才可以给出“强定价权”；如果只有单一优势或飞轮不成立，最多给“中等定价权”。

请给出：
1. 定价权水平
2. 定价权来源
3. 护城河三重检验结论
4. 详细分析（150字左右）

输出格式：
定价权: [强/中/弱/无]
来源: [来源1,来源2,...]
三重检验: [全过/部分通过/未通过/证据不足]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)

        # 解析定价权水平
        if "强定价权" in response or "强" in response[:50]:
            level = PricingPowerLevel.STRONG
            adjustment = 0.10
        elif "中等" in response or "中" in response[:50]:
            level = PricingPowerLevel.MODERATE
            adjustment = 0.05
        elif "弱" in response[:50]:
            level = PricingPowerLevel.WEAK
            adjustment = 0.0
        else:
            level = PricingPowerLevel.NONE
            adjustment = -0.10

        # 解析定价权来源
        sources = []
        if "品牌" in response:
            sources.append("品牌忠诚度")
        if "差异化" in response or "替代品" in response:
            sources.append("产品差异化")
        if "转换成本" in response:
            sources.append("客户转换成本高")
        if "供给" in response:
            sources.append("行业供给受限")
        if "刚需" in response:
            sources.append("刚需属性强")

        # 提取分析内容
        analysis_start = response.find("分析:")
        if analysis_start == -1:
            analysis_start = response.find("分析：")
        if analysis_start != -1:
            analysis = response[analysis_start + 3:].strip()
        else:
            analysis = response

        return PricingPowerAnalysis(
            level=level,
            adjustment=adjustment,
            sources=sources,
            analysis=analysis
        )
