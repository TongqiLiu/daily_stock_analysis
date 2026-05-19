"""
逆向投资检查器

别人贪婪我恐惧，别人恐惧我贪婪。
逆向投资不等于瞎抄底，关键判断：行业还好吗？公司还好吗？价格足够便宜吗？
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict


class MarketSentiment(Enum):
    """市场情绪"""
    EXTREME_PANIC = "极度恐慌"
    PESSIMISTIC = "悲观"
    NEUTRAL = "中性"
    OPTIMISTIC = "乐观"
    EXTREME_EUPHORIA = "极度狂热"


@dataclass
class ContrarianAnalysis:
    """逆向投资分析结果"""
    sentiment: MarketSentiment
    industry_ok: bool  # 行业还好吗
    company_ok: bool  # 公司还好吗
    price_ok: bool  # 价格够便宜吗

    should_contrarian_buy: bool  # 是否应逆向买入
    adjustment: float  # 总分调整 (±10%)
    analysis: str


class ContrarianChecker:
    """逆向投资检查器"""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def check(
        self,
        symbol: str,
        company_name: str,
        industry_score: float,
        company_score: float,
        valuation_score: float,
        market_data: Dict
    ) -> ContrarianAnalysis:
        """逆向投资检查"""

        # 1. 评估市场情绪
        sentiment = await self._assess_sentiment(symbol, company_name)

        # 2. 逆向三重验证
        industry_ok = industry_score >= 40
        company_ok = company_score >= 40
        price_ok = valuation_score >= 50

        # 3. 判断是否应逆向买入
        should_buy = False
        adjustment = 0.0

        if sentiment in [MarketSentiment.EXTREME_PANIC, MarketSentiment.PESSIMISTIC]:
            if industry_ok and company_ok and price_ok:
                should_buy = True
                adjustment = 0.10  # 加分 10%
        elif sentiment == MarketSentiment.EXTREME_EUPHORIA:
            adjustment = -0.10  # 扣分 10%

        # 生成分析
        analysis = self._generate_analysis(
            sentiment, industry_ok, company_ok, price_ok, should_buy
        )

        return ContrarianAnalysis(
            sentiment=sentiment,
            industry_ok=industry_ok,
            company_ok=company_ok,
            price_ok=price_ok,
            should_contrarian_buy=should_buy,
            adjustment=adjustment,
            analysis=analysis
        )

    async def _assess_sentiment(self, symbol: str, company_name: str) -> MarketSentiment:
        """评估市场情绪"""
        prompt = f"""
请评估 {company_name} ({symbol}) 当前的市场情绪：

1. 极度恐慌：所有人都说完蛋了、没救了
2. 悲观：多数人看空，媒体唱衰
3. 中性：关注度一般
4. 乐观：多数人看好，开始热炒
5. 极度狂热：所有人都在吹，连出租车司机都推荐

请从以上5种情绪中选择一种，并简要说明理由。

输出格式：
情绪: [1-5]
理由: [文字]
"""

        response = await self.llm_service.chat(prompt)

        # 解析情绪
        if "极度恐慌" in response or "1" in response[:20]:
            sentiment = MarketSentiment.EXTREME_PANIC
        elif "悲观" in response or "2" in response[:20]:
            sentiment = MarketSentiment.PESSIMISTIC
        elif "中性" in response or "3" in response[:20]:
            sentiment = MarketSentiment.NEUTRAL
        elif "乐观" in response or "4" in response[:20]:
            sentiment = MarketSentiment.OPTIMISTIC
        else:
            sentiment = MarketSentiment.EXTREME_EUPHORIA

        return sentiment

    def _generate_analysis(
        self,
        sentiment: MarketSentiment,
        industry_ok: bool,
        company_ok: bool,
        price_ok: bool,
        should_buy: bool
    ) -> str:
        """生成逆向投资分析"""
        lines = [
            f"市场情绪：{sentiment.value}",
            f"行业还好吗：{'✅ 是' if industry_ok else '❌ 否'}",
            f"公司还好吗：{'✅ 是' if company_ok else '❌ 否'}",
            f"价格够便宜吗：{'✅ 是' if price_ok else '❌ 否'}",
            "",
        ]

        if should_buy:
            lines.append("🟢 **逆向投资机会**：市场悲观但三好原则通过，可大胆买入")
        elif sentiment == MarketSentiment.EXTREME_EUPHORIA:
            lines.append("🔴 **远离**：市场极度狂热，即使三好也要谨慎")
        elif not all([industry_ok, company_ok, price_ok]):
            lines.append("🟡 **确认陷阱**：市场悲观确有道理，不是逆向机会")
        else:
            lines.append("⚪ **正常分析**：市场情绪中性，按正常流程评估")

        return "\n".join(lines)
