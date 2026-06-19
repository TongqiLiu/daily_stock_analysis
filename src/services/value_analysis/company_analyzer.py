"""
公司分析器

判断好公司的核心：护城河
评估维度：护城河类型与强度、盈利能力、财务健康度、管理层质量

评分标准：
- 80-100: A级公司（优质公司，护城河深厚）
- 60-79: B级公司（不错的公司，有竞争优势）
- 40-59: C级公司（一般公司，护城河薄弱）
- 0-39: D级公司（差公司，没有持久竞争优势）
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class MoatType(Enum):
    """护城河类型"""
    BRAND = "品牌护城河"
    COST = "成本护城河"
    SWITCHING_COST = "转换成本护城河"
    NETWORK_EFFECT = "网络效应护城河"
    LICENSE_PATENT = "牌照/专利护城河"


@dataclass
class MoatTripleTest:
    """护城河三重检验结果"""

    fundamental_law_status: str
    structure_status: str
    flywheel_status: str
    pricing_power_status: str
    fundamental_law_analysis: str
    structure_analysis: str
    flywheel_analysis: str
    pricing_power_analysis: str

    @property
    def passed_count(self) -> int:
        """通过的检验数量"""
        return sum(
            status == "通过"
            for status in [
                self.fundamental_law_status,
                self.structure_status,
                self.flywheel_status,
            ]
        )

    @property
    def all_passed(self) -> bool:
        """三关是否全过"""
        return self.passed_count == 3

    @property
    def conclusion(self) -> str:
        """三重检验结论"""
        statuses = [
            self.fundamental_law_status,
            self.structure_status,
            self.flywheel_status,
        ]
        if self.all_passed:
            return "全过"
        if any(status == "不确定" for status in statuses):
            return "证据不足"
        if self.passed_count > 0:
            return "部分通过"
        return "未通过"


class CompanyRating(Enum):
    """公司评级"""
    A = "A级公司"  # 80-100分
    B = "B级公司"  # 60-79分
    C = "C级公司"  # 40-59分
    D = "D级公司"  # 0-39分


@dataclass
class CompanyAnalysis:
    """公司分析结果"""
    # 四大维度评分
    moat_score: float  # 护城河 (0-40)
    profitability_score: float  # 盈利能力 (0-20)
    financial_health_score: float  # 财务健康 (0-20)
    management_score: float  # 管理层 (0-20)

    # 总分和评级
    total_score: float  # 总分 (0-100)
    rating: CompanyRating  # 评级

    # 详细分析
    moat_types: List[MoatType]  # 护城河类型
    moat_analysis: str
    profitability_analysis: str
    financial_health_analysis: str
    management_analysis: str

    # 关键发现
    key_findings: str
    risks: str

    # 关键指标（可选，默认值）
    gross_margin: Optional[float] = None
    roe: Optional[float] = None
    net_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    moat_triple_test: Optional[MoatTripleTest] = None


class CompanyAnalyzer:
    """公司分析器"""

    def __init__(self, llm_service):
        self.llm_service = llm_service

    async def analyze(
        self,
        symbol: str,
        company_name: str,
        industry_name: str,
        financial_data: Dict,
        market_data: Dict
    ) -> CompanyAnalysis:
        """
        分析公司

        Args:
            symbol: 股票代码
            company_name: 公司名称
            industry_name: 行业名称
            financial_data: 财务数据
            market_data: 市场数据

        Returns:
            CompanyAnalysis: 公司分析结果
        """
        # 1. 评估护城河
        moat_score, moat_types, moat_analysis, moat_triple_test = await self._analyze_moat(
            company_name, industry_name, financial_data, market_data
        )

        # 2. 评估盈利能力
        profitability_score, profitability_analysis = self._analyze_profitability(
            financial_data
        )

        # 3. 评估财务健康
        financial_health_score, financial_health_analysis = self._analyze_financial_health(
            financial_data
        )

        # 4. 评估管理层质量
        management_score, management_analysis = await self._analyze_management(
            company_name, financial_data, market_data
        )

        # 计算总分
        total_score = (
            moat_score +
            profitability_score +
            financial_health_score +
            management_score
        )

        # 确定评级
        rating = self._get_rating(total_score)

        # 提取关键指标
        gross_margin = financial_data.get('gross_margin')
        roe = financial_data.get('roe')
        net_margin = financial_data.get('net_margin')
        debt_ratio = financial_data.get('debt_ratio')

        # 生成关键发现和风险
        key_findings = self._generate_key_findings(
            total_score, moat_score, profitability_score,
            financial_health_score, management_score, moat_triple_test
        )
        risks = self._generate_risks(
            moat_score, financial_health_score, debt_ratio, moat_triple_test
        )

        return CompanyAnalysis(
            moat_score=moat_score,
            profitability_score=profitability_score,
            financial_health_score=financial_health_score,
            management_score=management_score,
            total_score=total_score,
            rating=rating,
            moat_types=moat_types,
            moat_analysis=moat_analysis,
            profitability_analysis=profitability_analysis,
            financial_health_analysis=financial_health_analysis,
            management_analysis=management_analysis,
            gross_margin=gross_margin,
            roe=roe,
            net_margin=net_margin,
            debt_ratio=debt_ratio,
            moat_triple_test=moat_triple_test,
            key_findings=key_findings,
            risks=risks
        )

    async def _analyze_moat(
        self,
        company_name: str,
        industry_name: str,
        financial_data: Dict,
        market_data: Dict
    ) -> tuple[float, List[MoatType], str, Optional[MoatTripleTest]]:
        """
        评估护城河 (0-40分)

        五种护城河（可叠加）：
        - 品牌护城河：提到名字就想买；品牌溢价明显 (10分)
        - 成本护城河：同样产品做到最低成本 (8分)
        - 转换成本护城河：客户换掉你麻烦得要死 (8分)
        - 网络效应护城河：用的人越多越好用 (8分)
        - 牌照/专利护城河：别人想进也进不来 (6分)
        """
        prompt = f"""
请分析 {company_name} 的护城河类型和强度：

1. 有哪些护城河类型？
   - 品牌护城河：提到名字就想买
   - 成本护城河：最低成本
   - 转换成本护城河：客户换掉你很麻烦
   - 网络效应护城河：用的人越多越好用
   - 牌照/专利护城河：进入壁垒

2. 护城河是否在加深？还是在被侵蚀？

3. 管理层是否在主动加固护城河？

4. 护城河三重检验（必须逐项判断）：
   - 底层规律：优势是否建立在人性、物理规律或经济规律上？
   - 结构：是否由多个优势互相咬合，而不是只有一个卖点？
   - 飞轮：优势是否能越转越快、自我强化？

三关全过，才可认定为强护城河并进一步支持定价权；只通过 1-2 关只能认定为阶段性或中等竞争优势。

评分：每种护城河类型给予相应分数，总分 0-40。

输出格式：
护城河类型: [类型1,类型2,...]
底层规律: [通过/不通过/不确定] - [证据]
结构: [通过/不通过/不确定] - [证据]
飞轮: [通过/不通过/不确定] - [证据]
定价权结论: [通过/不通过/不确定] - [三重检验对定价权的含义]
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)

        # 解析护城河类型
        moat_types = []

        def add_moat_type(moat_type: MoatType) -> None:
            if moat_type not in moat_types:
                moat_types.append(moat_type)

        lines = response.split('\n')
        for line in lines:
            if '品牌' in line:
                add_moat_type(MoatType.BRAND)
            if '成本' in line:
                add_moat_type(MoatType.COST)
            if '转换' in line:
                add_moat_type(MoatType.SWITCHING_COST)
            if '网络' in line:
                add_moat_type(MoatType.NETWORK_EFFECT)
            if '牌照' in line or '专利' in line:
                add_moat_type(MoatType.LICENSE_PATENT)

        score, analysis = self._parse_llm_response(response, max_score=40)
        moat_triple_test = self._parse_moat_triple_test(response)
        score = self._cap_moat_score_by_triple_test(score, moat_triple_test)

        return score, moat_types, analysis, moat_triple_test

    def _parse_moat_triple_test(self, response: str) -> Optional[MoatTripleTest]:
        """解析护城河三重检验结果"""
        fields = {
            "fundamental_law": ("底层规律",),
            "structure": ("结构", "结构≠一个点", "结构 != 一个点"),
            "flywheel": ("飞轮",),
            "pricing_power": ("定价权结论", "定价权"),
        }
        matched = {}
        for line in response.splitlines():
            normalized = line.strip().lstrip("-*0123456789.、) ")
            for key, labels in fields.items():
                if key in matched:
                    continue
                if any(normalized.startswith(label) for label in labels):
                    matched[key] = normalized
                    break

        if not matched:
            return None

        return MoatTripleTest(
            fundamental_law_status=self._parse_triple_status(matched.get("fundamental_law", "")),
            structure_status=self._parse_triple_status(matched.get("structure", "")),
            flywheel_status=self._parse_triple_status(matched.get("flywheel", "")),
            pricing_power_status=self._parse_triple_status(matched.get("pricing_power", "")),
            fundamental_law_analysis=self._parse_triple_analysis(matched.get("fundamental_law", "")),
            structure_analysis=self._parse_triple_analysis(matched.get("structure", "")),
            flywheel_analysis=self._parse_triple_analysis(matched.get("flywheel", "")),
            pricing_power_analysis=self._parse_triple_analysis(matched.get("pricing_power", "")),
        )

    def _parse_triple_status(self, line: str) -> str:
        """解析三重检验状态"""
        if not line:
            return "不确定"
        if any(word in line for word in ["不确定", "证据不足", "无法判断", "未知"]):
            return "不确定"
        if any(word in line for word in ["不通过", "未通过", "不成立", "不足", "缺乏", "否"]):
            return "不通过"
        if "通过" in line or "成立" in line:
            return "通过"
        return "不确定"

    def _parse_triple_analysis(self, line: str) -> str:
        """解析三重检验说明"""
        if not line:
            return "未提供明确证据"
        for delimiter in [" - ", "：", ":"]:
            if delimiter in line:
                return line.split(delimiter, 1)[1].strip()
        return line.strip()

    def _cap_moat_score_by_triple_test(
        self,
        score: float,
        moat_triple_test: Optional[MoatTripleTest],
    ) -> float:
        """三重检验未全过时限制护城河高分，避免单点优势被误判为强护城河"""
        if moat_triple_test is None or moat_triple_test.all_passed:
            return score

        if moat_triple_test.passed_count == 2:
            return min(score, 30)
        if moat_triple_test.passed_count == 1:
            return min(score, 20)
        return min(score, 10)

    def _analyze_profitability(self, financial_data: Dict) -> tuple[float, str]:
        """
        评估盈利能力 (0-20分)

        指标：毛利率、ROE、净利率
        优秀: >50%毛利率, >20%ROE, >20%净利率 (15-20分)
        良好: 30-50%, 15-20%, 10-20% (10-15分)
        一般: 15-30%, 10-15%, 5-10% (5-10分)
        差: <15%, <10%, <5% (0-5分)
        """
        gross_margin = financial_data.get('gross_margin', 0)
        roe = financial_data.get('roe', 0)
        net_margin = financial_data.get('net_margin', 0)

        # 计算各指标得分
        gm_score = self._score_margin(gross_margin, [15, 30, 50])
        roe_score = self._score_margin(roe, [10, 15, 20])
        nm_score = self._score_margin(net_margin, [5, 10, 20])

        total_score = (gm_score + roe_score + nm_score) / 3 * 20

        analysis = f"""
毛利率: {gross_margin:.1%} ({self._get_level(gm_score)})
ROE: {roe:.1%} ({self._get_level(roe_score)})
净利率: {net_margin:.1%} ({self._get_level(nm_score)})

综合评价: {self._get_profitability_comment(total_score)}
"""

        return total_score, analysis

    def _analyze_financial_health(self, financial_data: Dict) -> tuple[float, str]:
        """
        评估财务健康度 (0-20分)

        指标：资产负债率、经营现金流、自由现金流、有息负债率
        """
        debt_ratio = financial_data.get('debt_ratio', 0)
        ocf = financial_data.get('operating_cash_flow', 0)
        fcf = financial_data.get('free_cash_flow', 0)
        interest_debt_ratio = financial_data.get('interest_debt_ratio', 0)

        score = 20.0

        # 资产负债率扣分
        if debt_ratio > 0.7:
            score -= 5
        elif debt_ratio > 0.5:
            score -= 3
        elif debt_ratio > 0.3:
            score -= 1

        # 现金流扣分
        if ocf <= 0:
            score -= 5
        if fcf <= 0:
            score -= 3

        # 有息负债率扣分
        if interest_debt_ratio > 0.6:
            score -= 4
        elif interest_debt_ratio > 0.4:
            score -= 2

        score = max(0, score)

        analysis = f"""
资产负债率: {debt_ratio:.1%}
经营现金流: {'持续为正' if ocf > 0 else '为负'}
自由现金流: {'充沛' if fcf > 0 else '紧张'}
有息负债率: {interest_debt_ratio:.1%}

综合评价: {self._get_financial_health_comment(score)}
"""

        return score, analysis

    async def _analyze_management(
        self,
        company_name: str,
        financial_data: Dict,
        market_data: Dict
    ) -> tuple[float, str]:
        """
        评估管理层质量 (0-20分)

        维度：诚信、能力、股东回报意识、战略清晰度
        """
        prompt = f"""
请评估 {company_name} 管理层质量：

1. 诚信：是否有财务造假/违规历史？
2. 能力：过去5年业绩是否持续增长？
3. 股东回报意识：分红/回购是否积极？
4. 战略清晰度：业务是否聚焦，有没有瞎搞多元化？

评分标准：每个维度 0-5 分，总分 0-20。

输出格式：
评分: [数字]
分析: [文字]
"""

        response = await self.llm_service.chat(prompt)
        score, analysis = self._parse_llm_response(response, max_score=20)

        return score, analysis

    def _score_margin(self, value: float, thresholds: List[float]) -> float:
        """根据阈值对指标打分 (0-1)"""
        if value >= thresholds[2]:
            return 1.0
        elif value >= thresholds[1]:
            return 0.75
        elif value >= thresholds[0]:
            return 0.5
        else:
            return 0.25

    def _get_level(self, score: float) -> str:
        """获取水平描述"""
        if score >= 0.9:
            return "优秀"
        elif score >= 0.7:
            return "良好"
        elif score >= 0.5:
            return "一般"
        else:
            return "较差"

    def _get_profitability_comment(self, score: float) -> str:
        """获取盈利能力评价"""
        if score >= 15:
            return "盈利能力优秀，具备显著竞争优势"
        elif score >= 10:
            return "盈利能力良好"
        elif score >= 5:
            return "盈利能力一般"
        else:
            return "盈利能力偏弱"

    def _get_financial_health_comment(self, score: float) -> str:
        """获取财务健康评价"""
        if score >= 15:
            return "财务状况健康"
        elif score >= 10:
            return "财务状况一般"
        else:
            return "财务状况需要关注"

    def _parse_llm_response(self, response: str, max_score: float) -> tuple[float, str]:
        """解析 LLM 返回的评分和分析"""
        lines = response.strip().split('\n')
        score = 0.0
        analysis = ""

        for line in lines:
            if line.startswith("评分:") or line.startswith("评分："):
                try:
                    score_str = line.replace("：", ":", 1).split(':', 1)[1].strip()
                    score = float(score_str.split()[0])
                    score = min(score, max_score)
                except (IndexError, ValueError):
                    logger.warning(f"无法解析评分: {line}")
            elif line.startswith("分析:") or line.startswith("分析："):
                analysis = line.replace("：", ":", 1).split(':', 1)[1].strip()
            elif analysis:
                analysis += "\n" + line

        return score, analysis

    def _get_rating(self, total_score: float) -> CompanyRating:
        """根据总分确定评级"""
        if total_score >= 80:
            return CompanyRating.A
        elif total_score >= 60:
            return CompanyRating.B
        elif total_score >= 40:
            return CompanyRating.C
        else:
            return CompanyRating.D

    def _generate_key_findings(
        self,
        total_score: float,
        moat: float,
        profitability: float,
        financial: float,
        management: float,
        moat_triple_test: Optional[MoatTripleTest] = None
    ) -> str:
        """生成关键发现"""
        findings = []

        if total_score >= 80:
            findings.append("✅ 优质公司，护城河深厚")
        elif total_score >= 60:
            findings.append("🟢 不错的公司，有竞争优势")
        elif total_score >= 40:
            findings.append("🟡 一般公司，护城河薄弱")
        else:
            findings.append("🔴 公司竞争力不足")

        if moat >= 30:
            findings.append("💪 护城河深厚，长期竞争优势明显")
        elif moat <= 10:
            findings.append("⚠️ 护城河薄弱，竞争优势不明显")

        if moat_triple_test:
            if moat_triple_test.all_passed:
                findings.append("✅ 护城河三重检验全过，具备进一步验证定价权的基础")
            else:
                findings.append(f"⚠️ 护城河三重检验{moat_triple_test.conclusion}，定价权证据不足")

        if profitability >= 15:
            findings.append("📈 盈利能力优秀")
        elif profitability <= 5:
            findings.append("📉 盈利能力偏弱")

        return "\n".join(findings)

    def _generate_risks(
        self,
        moat: float,
        financial: float,
        debt_ratio: Optional[float],
        moat_triple_test: Optional[MoatTripleTest] = None
    ) -> str:
        """生成风险提示"""
        risks = []

        if moat <= 10:
            risks.append("⚠️ 护城河薄弱，长期竞争力存疑")

        if financial <= 10:
            risks.append("⚠️ 财务健康度较差")

        if debt_ratio and debt_ratio > 0.7:
            risks.append("⚠️ 资产负债率过高，财务风险较大")

        if moat_triple_test and not moat_triple_test.all_passed:
            risks.append("⚠️ 护城河三重检验未全过，长期定价权需要重新验证")

        if not risks:
            risks.append("✅ 公司整体风险可控")

        return "\n".join(risks)
