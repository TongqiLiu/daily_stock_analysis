"""
行业分析器

评估行业的"先天属性"，决定了赚钱的难易程度。
四大评估维度：行业格局、定价权、需求稳定性、进入壁垒。

评分标准：
- 80-100: A级行业（优质行业，先天属性好）
- 60-79: B级行业（不错的行业，有明显优势）
- 40-59: C级行业（一般行业，需要精选公司）
- 0-39: D级行业（差行业，慎入）
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class IndustryRating(Enum):
    """行业评级"""
    A = "A级行业"  # 80-100分
    B = "B级行业"  # 60-79分
    C = "C级行业"  # 40-59分
    D = "D级行业"  # 0-39分


@dataclass
class IndustryAnalysis:
    """行业分析结果"""
    # 四大维度评分
    market_structure_score: float  # 行业格局 (0-25)
    pricing_power_score: float  # 定价权 (0-25)
    demand_stability_score: float  # 需求稳定性 (0-25)
    entry_barrier_score: float  # 进入壁垒 (0-25)

    # 总分和评级
    total_score: float  # 总分 (0-100)
    rating: IndustryRating  # 评级

    # 分析详情
    market_structure_analysis: str
    pricing_power_analysis: str
    demand_stability_analysis: str
    entry_barrier_analysis: str

    # 关键发现
    key_findings: str
    risks: str


class IndustryAnalyzer:
    """行业分析器"""

    def __init__(self, llm_service):
        """
        Args:
            llm_service: LLM 服务，用于分析行业特征
        """
        self.llm_service = llm_service

    async def analyze(
        self,
        symbol: str,
        industry_name: str,
        company_name: str,
        market_data: Dict
    ) -> IndustryAnalysis:
        """
        分析行业

        Args:
            symbol: 股票代码
            industry_name: 行业名称
            company_name: 公司名称
            market_data: 市场数据

        Returns:
            IndustryAnalysis: 行业分析结果
        """
        # 1. 评估行业格局
        market_structure_score, market_structure_analysis = await self._analyze_market_structure(
            industry_name, company_name, market_data
        )

        # 2. 评估定价权
        pricing_power_score, pricing_power_analysis = await self._analyze_pricing_power(
            industry_name, company_name, market_data
        )

        # 3. 评估需求稳定性
        demand_stability_score, demand_stability_analysis = await self._analyze_demand_stability(
            industry_name, market_data
        )

        # 4. 评估进入壁垒
        entry_barrier_score, entry_barrier_analysis = await self._analyze_entry_barrier(
            industry_name, company_name, market_data
        )

        # 计算总分
        total_score = (
            market_structure_score +
            pricing_power_score +
            demand_stability_score +
            entry_barrier_score
        )

        # 确定评级
        rating = self._get_rating(total_score)

        # 生成关键发现和风险
        key_findings = self._generate_key_findings(
            total_score, market_structure_score, pricing_power_score,
            demand_stability_score, entry_barrier_score
        )
        risks = self._generate_risks(
            total_score, pricing_power_score, demand_stability_score
        )

        return IndustryAnalysis(
            market_structure_score=market_structure_score,
            pricing_power_score=pricing_power_score,
            demand_stability_score=demand_stability_score,
            entry_barrier_score=entry_barrier_score,
            total_score=total_score,
            rating=rating,
            market_structure_analysis=market_structure_analysis,
            pricing_power_analysis=pricing_power_analysis,
            demand_stability_analysis=demand_stability_analysis,
            entry_barrier_analysis=entry_barrier_analysis,
            key_findings=key_findings,
            risks=risks
        )

    async def _analyze_market_structure(
        self,
        industry_name: str,
        company_name: str,
        market_data: Dict
    ) -> tuple[float, str]:
        """
        评估行业格局 (0-25分)

        标准：
        - 寡头垄断（3家以内分天下）→ 20-25分
        - 双寡头 → 15-20分
        - 几家头部+长尾 → 10-15分
        - 完全竞争（一堆人打成狗）→ 0-10分
        """
        prompt = f"""
请分析 {industry_name} 行业的竞争格局：

1. 这个行业有几家主要玩家？市场份额分布如何？
2. 是寡头垄断、双寡头、几家头部+长尾，还是完全竞争？
3. {company_name} 在行业中的地位如何？

评分标准：
- 寡头垄断（3家以内分天下）→ 20-25分
- 双寡头 → 15-20分
- 几家头部+长尾 → 10-15分
- 完全竞争（一堆人打成狗）→ 0-10分

请给出评分（0-25）和详细分析（200字左右）。

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=25)

        return score, analysis

    async def _analyze_pricing_power(
        self,
        industry_name: str,
        company_name: str,
        market_data: Dict
    ) -> tuple[float, str]:
        """
        评估定价权 (0-25分) ⭐最关键

        标准：
        - 可以主动提价且客户不流失 → 20-25分
        - 可以跟随通胀温和提价 → 15-20分
        - 价格基本由市场决定 → 5-15分
        - 客户/下游说了算，自己没话语权 → 0-5分

        关键问题：这个行业的公司能不能涨价？涨了客户还买不买？
        """
        prompt = f"""
请分析 {industry_name} 行业的定价权：

1. 这个行业的公司能不能主动涨价？
2. 涨价后客户会流失吗？
3. 定价权来自哪里？（品牌、产品差异化、转换成本、供给受限等）
4. {company_name} 的定价权如何？

评分标准：
- 可以主动提价且客户不流失 → 20-25分
- 可以跟随通胀温和提价 → 15-20分
- 价格基本由市场决定 → 5-15分
- 客户/下游说了算，自己没话语权 → 0-5分

请给出评分（0-25）和详细分析（200字左右）。

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=25)

        return score, analysis

    async def _analyze_demand_stability(
        self,
        industry_name: str,
        market_data: Dict
    ) -> tuple[float, str]:
        """
        评估需求稳定性 (0-25分)

        标准：
        - 刚需+永续需求（永远都有人买）→ 20-25分
        - 弱周期（有波动但长期稳定）→ 15-20分
        - 明显周期性 → 5-15分
        - 一阵风/政策驱动 → 0-5分

        关键问题：这东西10年后还有人买吗？
        """
        prompt = f"""
请分析 {industry_name} 行业的需求稳定性：

1. 这是刚需还是可选消费？
2. 需求是永续的还是周期性的？
3. 10年后还有人买吗？
4. 是否受政策驱动？

评分标准：
- 刚需+永续需求（永远都有人买）→ 20-25分
- 弱周期（有波动但长期稳定）→ 15-20分
- 明显周期性 → 5-15分
- 一阵风/政策驱动 → 0-5分

请给出评分（0-25）和详细分析（200字左右）。

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=25)

        return score, analysis

    async def _analyze_entry_barrier(
        self,
        industry_name: str,
        company_name: str,
        market_data: Dict
    ) -> tuple[float, str]:
        """
        评估进入壁垒/护城河 (0-25分)

        标准：
        - 极高壁垒（牌照/专利/网络效应/品牌）→ 20-25分
        - 较高壁垒（规模效应+品牌+渠道）→ 15-20分
        - 中等壁垒（有一定技术门槛）→ 5-15分
        - 低壁垒（谁都能进来）→ 0-5分

        关键问题：别人想进来抢饭碗，难不难？
        """
        prompt = f"""
请分析 {industry_name} 行业的进入壁垒：

1. 新玩家进入这个行业难不难？
2. 壁垒来自哪里？（牌照、专利、网络效应、品牌、规模、技术等）
3. {company_name} 的壁垒强不强？
4. 这些壁垒是否在加深？

评分标准：
- 极高壁垒（牌照/专利/网络效应/品牌）→ 20-25分
- 较高壁垒（规模效应+品牌+渠道）→ 15-20分
- 中等壁垒（有一定技术门槛）→ 5-15分
- 低壁垒（谁都能进来）→ 0-5分

请给出评分（0-25）和详细分析（200字左右）。

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=25)

        return score, analysis

    def _parse_llm_response(self, response: str, max_score: float) -> tuple[float, str]:
        """解析 LLM 返回的评分和分析"""
        lines = response.strip().split('\n')
        score = 0.0
        analysis = ""

        for line in lines:
            if line.startswith("评分:") or line.startswith("评分："):
                try:
                    score_str = line.split(':', 1)[1].strip()
                    score = float(score_str.split()[0])
                    score = min(score, max_score)  # 不超过最大值
                except:
                    logger.warning(f"无法解析评分: {line}")
            elif line.startswith("分析:") or line.startswith("分析："):
                analysis = line.split(':', 1)[1].strip()
            elif analysis:  # 继续追加分析内容
                analysis += "\n" + line

        return score, analysis

    def _get_rating(self, total_score: float) -> IndustryRating:
        """根据总分确定评级"""
        if total_score >= 80:
            return IndustryRating.A
        elif total_score >= 60:
            return IndustryRating.B
        elif total_score >= 40:
            return IndustryRating.C
        else:
            return IndustryRating.D

    def _generate_key_findings(
        self,
        total_score: float,
        market_structure: float,
        pricing_power: float,
        demand: float,
        barrier: float
    ) -> str:
        """生成关键发现"""
        findings = []

        if total_score >= 80:
            findings.append("✅ 优质行业，先天属性优秀")
        elif total_score >= 60:
            findings.append("🟢 不错的行业，具备明显优势")
        elif total_score >= 40:
            findings.append("🟡 一般行业，需要精选优质公司")
        else:
            findings.append("🔴 行业先天不足，需谨慎")

        # 找出最强和最弱维度
        scores = {
            "行业格局": market_structure,
            "定价权": pricing_power,
            "需求稳定性": demand,
            "进入壁垒": barrier
        }
        strongest = max(scores, key=scores.get)
        weakest = min(scores, key=scores.get)

        findings.append(f"💪 最强维度：{strongest} ({scores[strongest]:.0f}/25)")
        findings.append(f"⚠️ 最弱维度：{weakest} ({scores[weakest]:.0f}/25)")

        # 定价权特别重要
        if pricing_power >= 20:
            findings.append("⭐ 定价权强，这是最关键的指标")
        elif pricing_power <= 10:
            findings.append("🔴 定价权弱，长期盈利能力存疑")

        return "\n".join(findings)

    def _generate_risks(
        self,
        total_score: float,
        pricing_power: float,
        demand: float
    ) -> str:
        """生成风险提示"""
        risks = []

        if total_score < 40:
            risks.append("⚠️ 行业整体得分低，即使个别公司优秀也难逆天改命")

        if pricing_power < 10:
            risks.append("⚠️ 定价权弱，容易陷入价格战，盈利能力不稳定")

        if demand < 10:
            risks.append("⚠️ 需求不稳定，可能是周期性或政策驱动型行业")

        if not risks:
            risks.append("✅ 行业整体风险可控")

        return "\n".join(risks)
