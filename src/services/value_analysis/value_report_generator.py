"""
价值投资报告生成器

整合所有分析结果，生成完整的价值投资分析报告。
基于邱国鹭《投资中最简单的事》的投资方法论。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Optional
import logging

from .industry_analyzer import IndustryAnalysis
from .company_analyzer import CompanyAnalysis
from .valuation_analyzer import ValuationAnalysis
from .contrarian_checker import ContrarianAnalysis
from .pricing_power_analyzer import PricingPowerAnalysis
from .data_validator import DataValidator

logger = logging.getLogger(__name__)


class InvestmentConclusion(Enum):
    """投资结论"""
    STRONG_BUY = "⭐ 强烈推荐"  # 80-100
    BUY = "🟢 推荐"  # 70-79
    WATCH = "🟡 可关注"  # 60-69
    HOLD = "🟠 观望"  # 50-59
    AVOID = "🔴 不推荐"  # 40-49
    STAY_AWAY = "❌ 远离"  # <40


@dataclass
class ValueInvestmentReport:
    """价值投资报告"""
    # 基本信息
    symbol: str
    company_name: str
    industry_name: str
    analysis_date: datetime
    current_price: float
    market_cap: float

    # 五大分析结果
    industry_analysis: IndustryAnalysis
    company_analysis: CompanyAnalysis
    valuation_analysis: ValuationAnalysis
    contrarian_analysis: ContrarianAnalysis
    pricing_power_analysis: PricingPowerAnalysis

    # 综合评分
    industry_score: float  # 30%
    company_score: float  # 35%
    valuation_score: float  # 25%
    contrarian_adjustment: float  # ±10%
    pricing_power_adjustment: float  # ±10%
    final_score: float

    # 投资结论
    conclusion: InvestmentConclusion
    recommendation: str
    suggested_price_range: Optional[tuple[float, float]]
    suggested_position_size: Optional[str]
    max_risk: str

    # 一票否决项检查
    veto_triggered: bool
    veto_reasons: list

    # 数据校验
    data_validation_report: str


class ValueReportGenerator:
    """价值投资报告生成器"""

    # 权重配置
    WEIGHTS = {
        'industry': 0.30,
        'company': 0.35,
        'valuation': 0.25,
    }

    # 一票否决项
    VETO_CONDITIONS = [
        ('industry_score', '<', 30, '行业评分 < 30（夕阳行业）'),
        ('company_moat_score', '<', 10, '公司护城河评分 < 10（完全没有竞争优势）'),
        ('management_integrity', '=', 0, '管理层诚信评分 = 0（财务造假/重大违规）'),
        ('operating_cash_flow_negative_years', '>=', 3, '连续3年经营现金流为负'),
        ('interest_debt_ratio', '>', 0.80, '有息负债率 > 80% 且无合理解释'),
    ]

    def __init__(self):
        pass

    def generate(
        self,
        symbol: str,
        company_name: str,
        industry_name: str,
        industry_analysis: IndustryAnalysis,
        company_analysis: CompanyAnalysis,
        valuation_analysis: ValuationAnalysis,
        contrarian_analysis: ContrarianAnalysis,
        pricing_power_analysis: PricingPowerAnalysis,
        market_data: Dict,
        data_validator: Optional[DataValidator] = None
    ) -> ValueInvestmentReport:
        """
        生成价值投资报告

        Args:
            symbol: 股票代码
            company_name: 公司名称
            industry_name: 行业名称
            industry_analysis: 行业分析结果
            company_analysis: 公司分析结果
            valuation_analysis: 估值分析结果
            contrarian_analysis: 逆向投资分析结果
            pricing_power_analysis: 定价权分析结果
            market_data: 市场数据
            data_validator: 数据校验器（可选）

        Returns:
            ValueInvestmentReport: 完整报告
        """

        # 计算加权总分
        industry_score = industry_analysis.total_score
        company_score = company_analysis.total_score
        valuation_score = valuation_analysis.total_score

        base_score = (
            industry_score * self.WEIGHTS['industry'] +
            company_score * self.WEIGHTS['company'] +
            valuation_score * self.WEIGHTS['valuation']
        )

        # 逆向投资和定价权调整
        contrarian_adj = contrarian_analysis.adjustment
        pricing_adj = pricing_power_analysis.adjustment

        final_score = base_score * (1 + contrarian_adj + pricing_adj)
        final_score = max(0, min(100, final_score))  # 限制在 0-100

        # 一票否决项检查
        veto_triggered, veto_reasons = self._check_veto_conditions(
            industry_analysis, company_analysis, valuation_analysis, market_data
        )

        # 确定投资结论
        conclusion = self._get_conclusion(final_score, veto_triggered)

        # 生成投资建议
        recommendation = self._generate_recommendation(
            conclusion, industry_analysis, company_analysis,
            valuation_analysis, contrarian_analysis
        )

        # 建议买入价格区间
        current_price = market_data.get('current_price', 0)
        suggested_price_range = self._suggest_price_range(
            current_price, valuation_score, conclusion
        )

        # 建议仓位
        suggested_position_size = self._suggest_position_size(conclusion, final_score)

        # 最大风险
        max_risk = self._identify_max_risk(
            industry_analysis, company_analysis, valuation_analysis
        )

        # 数据校验报告
        data_validation_report = ""
        if data_validator:
            data_validation_report = data_validator.generate_validation_report()

        return ValueInvestmentReport(
            symbol=symbol,
            company_name=company_name,
            industry_name=industry_name,
            analysis_date=datetime.now(),
            current_price=current_price,
            market_cap=market_data.get('market_cap', 0),
            industry_analysis=industry_analysis,
            company_analysis=company_analysis,
            valuation_analysis=valuation_analysis,
            contrarian_analysis=contrarian_analysis,
            pricing_power_analysis=pricing_power_analysis,
            industry_score=industry_score,
            company_score=company_score,
            valuation_score=valuation_score,
            contrarian_adjustment=contrarian_adj,
            pricing_power_adjustment=pricing_adj,
            final_score=final_score,
            conclusion=conclusion,
            recommendation=recommendation,
            suggested_price_range=suggested_price_range,
            suggested_position_size=suggested_position_size,
            max_risk=max_risk,
            veto_triggered=veto_triggered,
            veto_reasons=veto_reasons,
            data_validation_report=data_validation_report
        )

    def format_markdown(self, report: ValueInvestmentReport) -> str:
        """格式化为 Markdown 报告"""

        md = f"""# 价值投资分析报告

> 基于邱国鹭《投资中最简单的事》的投资方法论
> 核心理念：**用合理的价格，买好行业里的好公司，然后拿得住**

## 📋 一、分析概要

| 项目 | 内容 |
|------|------|
| 分析标的 | {report.company_name}（{report.symbol}） |
| 所属行业 | {report.industry_name} |
| 分析日期 | {report.analysis_date.strftime('%Y-%m-%d')} |
| 当前股价 | ¥{report.current_price:.2f} |
| 总市值 | {report.market_cap / 1e8:.2f} 亿 |
| **综合评分** | **{report.final_score:.1f}/100** |
| **投资结论** | **{report.conclusion.value}** |
"""

        if report.veto_triggered:
            md += "\n### ⚠️ 一票否决项\n\n"
            for reason in report.veto_reasons:
                md += f"- 🔴 {reason}\n"
            md += "\n"

        md += f"""
## 🏭 二、行业分析（{report.industry_score:.1f}/100分）

### 评分明细

| 维度 | 评分 | 分析 |
|------|:----:|------|
| 行业格局 | {report.industry_analysis.market_structure_score:.0f}/25 | {report.industry_analysis.market_structure_analysis[:100]}... |
| 定价权 ⭐ | {report.industry_analysis.pricing_power_score:.0f}/25 | {report.industry_analysis.pricing_power_analysis[:100]}... |
| 需求稳定性 | {report.industry_analysis.demand_stability_score:.0f}/25 | {report.industry_analysis.demand_stability_analysis[:100]}... |
| 进入壁垒 | {report.industry_analysis.entry_barrier_score:.0f}/25 | {report.industry_analysis.entry_barrier_analysis[:100]}... |

**行业评级：{report.industry_analysis.rating.value}**

### 关键发现
{report.industry_analysis.key_findings}

### 风险提示
{report.industry_analysis.risks}

## 🏢 三、公司分析（{report.company_score:.1f}/100分）

### 评分明细

| 维度 | 评分 | 说明 |
|------|:----:|------|
| 护城河 | {report.company_analysis.moat_score:.0f}/40 | 护城河类型：{', '.join([m.value for m in report.company_analysis.moat_types])} |
| 盈利能力 | {report.company_analysis.profitability_score:.0f}/20 | 毛利率 {report.company_analysis.gross_margin:.1%} / ROE {report.company_analysis.roe:.1%} |
| 财务健康 | {report.company_analysis.financial_health_score:.0f}/20 | 资产负债率 {report.company_analysis.debt_ratio:.1%} |
| 管理层质量 | {report.company_analysis.management_score:.0f}/20 | - |

**公司评级：{report.company_analysis.rating.value}**

{self._format_moat_triple_test(report.company_analysis)}

### 关键发现
{report.company_analysis.key_findings}

## 💰 四、估值分析（{report.valuation_score:.1f}/100分）

### 评分明细

| 维度 | 评分 | 说明 |
|------|:----:|------|
| PE 分析 | {report.valuation_analysis.pe_score:.0f}/25 | PE是结果不是原因 |
| 辅助估值 | {report.valuation_analysis.supplementary_score:.0f}/25 | PB/PS/PEG |
| 历史分位 | {report.valuation_analysis.historical_percentile_score:.0f}/25 | 当前估值水平 |
| 安全边际 | {report.valuation_analysis.safety_margin_score:.0f}/25 | 投资保护垫 |

### ⚠️ 估值陷阱检查
{report.valuation_analysis.trap_warnings}

## 🔄 五、逆向投资 & 定价权

### 逆向投资检查
{report.contrarian_analysis.analysis}

**总分调整：{report.contrarian_adjustment:+.0%}**

### 定价权分析
**定价权水平：{report.pricing_power_analysis.level.value}**

定价权来源：{', '.join(report.pricing_power_analysis.sources) if report.pricing_power_analysis.sources else '暂无明显定价权'}

{report.pricing_power_analysis.analysis}

**总分调整：{report.pricing_power_adjustment:+.0%}**

{self._format_stock_selection_checklist(report)}

{self._format_exit_discipline(report)}

## 🎯 八、投资结论

### 综合评分

| 维度 | 评分 | 权重 | 加权分 |
|------|:----:|:----:|:------:|
| 行业 | {report.industry_score:.1f} | 30% | {report.industry_score * 0.3:.1f} |
| 公司 | {report.company_score:.1f} | 35% | {report.company_score * 0.35:.1f} |
| 价格 | {report.valuation_score:.1f} | 25% | {report.valuation_score * 0.25:.1f} |
| 逆向调整 | {report.contrarian_adjustment:+.0%} | - | {(report.contrarian_adjustment * (report.industry_score * 0.3 + report.company_score * 0.35 + report.valuation_score * 0.25)):+.1f} |
| 定价权调整 | {report.pricing_power_adjustment:+.0%} | - | {(report.pricing_power_adjustment * (report.industry_score * 0.3 + report.company_score * 0.35 + report.valuation_score * 0.25)):+.1f} |
| **最终得分** | **{report.final_score:.1f}** | - | - |

### 投资建议

**结论：{report.conclusion.value}**

{report.recommendation}

"""

        if report.suggested_price_range:
            low, high = report.suggested_price_range
            md += f"\n**建议买入价格区间：¥{low:.2f} - ¥{high:.2f}**\n"

        if report.suggested_position_size:
            md += f"\n**建议仓位：{report.suggested_position_size}**\n"

        md += f"""
**最大风险：{report.max_risk}**

## ⚠️ 九、风险提示

> 本分析基于邱国鹭《投资中最简单的事》的价值投资方法论，仅作为投资思考框架使用，不构成具体投资建议。
>
> 投资有风险，决策需谨慎。知道和做到之间隔着一条太平洋。

{report.data_validation_report}

---

*报告生成时间: {report.analysis_date.strftime('%Y-%m-%d %H:%M:%S')}*
"""

        return md

    def _check_veto_conditions(
        self,
        industry: IndustryAnalysis,
        company: CompanyAnalysis,
        valuation: ValuationAnalysis,
        market_data: Dict
    ) -> tuple[bool, list]:
        """检查一票否决项"""
        veto_reasons = []

        if industry.total_score < 30:
            veto_reasons.append("行业评分 < 30（夕阳行业）")

        if company.moat_score < 10:
            veto_reasons.append("公司护城河评分 < 10（完全没有竞争优势）")

        if company.moat_triple_test and not company.moat_triple_test.all_passed:
            veto_reasons.append("护城河三重检验未全过（定价权证据不足）")

        if company.management_score == 0:
            veto_reasons.append("管理层诚信评分 = 0（财务造假/重大违规）")

        # 更多一票否决项检查...

        return len(veto_reasons) > 0, veto_reasons

    def _format_moat_triple_test(self, company: CompanyAnalysis) -> str:
        """格式化护城河三重检验"""
        triple = company.moat_triple_test
        if not triple:
            return ""

        pricing_power_implication = (
            "三关全过，可进一步验证强定价权"
            if triple.all_passed
            else "三关未全过，不能直接认定强定价权"
        )

        return f"""### 护城河三重检验

| 检验项 | 结果 | 核心证据 |
|------|:----:|------|
| 底层规律 | {triple.fundamental_law_status} | {triple.fundamental_law_analysis} |
| 结构不是一个点 | {triple.structure_status} | {triple.structure_analysis} |
| 飞轮 | {triple.flywheel_status} | {triple.flywheel_analysis} |

**三关结论：{triple.conclusion}**

**定价权含义：{pricing_power_implication}**。{triple.pricing_power_analysis}
"""

    def _format_stock_selection_checklist(self, report: ValueInvestmentReport) -> str:
        """格式化价值选股加分项"""
        founder_status, founder_basis, founder_invalidation = self._assess_founder_led(report.company_analysis)
        core_status, core_basis, core_invalidation = self._assess_core_position(report.industry_analysis)
        flywheel_status, flywheel_basis, flywheel_invalidation = self._assess_quality_flywheel(report.company_analysis)
        timing_status, timing_basis, timing_invalidation = self._assess_why_now(
            report.valuation_analysis,
            report.contrarian_analysis,
        )

        return f"""## 🧭 六、价值选股加分项

> 用于提高候选股置信度和仓位上限；不能替代估值、安全边际和护城河判断。

| 加分项 | 结果 | 关键依据 | 证伪信号 |
|------|:----:|------|------|
| Founder-led / 创始人式领导者 | {founder_status} | {founder_basis} | {founder_invalidation} |
| 核心位置 | {core_status} | {core_basis} | {core_invalidation} |
| 底层规律的飞轮 | {flywheel_status} | {flywheel_basis} | {flywheel_invalidation} |
| 为什么是现在 | {timing_status} | {timing_basis} | {timing_invalidation} |

### 五件事备忘
- **核心论点**：{report.recommendation}
- **关键假设**：行业优势、公司护城河、盈利质量和估值安全边际需要同时维持。
- **证伪信号**：护城河被侵蚀、盈利质量下滑、催化剂落空或估值脱离基本面。
- **为什么是这个载体**：优先选择处在趋势核心、议价能力更强、财务质量更好的公司；若证据不足，需要和同行或 ETF 对比。
- **兑现时间窗口**：默认观察未来 1-2 年的业绩、现金流、订单、份额或定价权兑现。
"""

    def _format_exit_discipline(self, report: ValueInvestmentReport) -> str:
        """格式化退出纪律"""
        narrative_risk = self._assess_narrative_premium_risk(report)
        if narrative_risk == "高":
            action = "减仓 / 退出"
        elif narrative_risk == "中":
            action = "降低仓位上限 / 分批减仓"
        else:
            action = "继续观察"

        return f"""## 🚪 七、退出纪律

### 叙事溢价削减

**风险等级：{narrative_risk}**

如果公司基本面没有实质进展，只是靠情绪、主题、故事或估值倍数扩张上涨，应视为借来的涨幅，后续需要用业绩、现金流、订单、份额或定价权归还。

### 证伪信号与动作

| 证伪信号 | 应对动作 |
|------|------|
| 核心假设被证伪，需求、份额、毛利率、ROE 或现金流未改善 | {action} |
| 未来 1-2 年催化剂落空或兑现明显延后 | 降低仓位上限 |
| 股价上涨主要来自估值倍数扩张，缺少基本面支撑 | 减仓 |
| 估值进入历史高分位，但护城河和盈利质量没有同步增强 | 减仓 / 退出 |

**退出纪律一句话**：错了知道怎么走，不用事后临场找理由。
"""

    def _assess_founder_led(self, company: CompanyAnalysis) -> tuple[str, str, str]:
        """评估 Founder-led / 类创始人管理层"""
        management_text = company.management_analysis or ""
        if any(keyword in management_text for keyword in ["创始", "owner", "长期", "股东回报", "回购"]):
            return "通过", "管理层分析中出现长期主义、创始人式或股东回报证据", "关键人退出、乱并购、短期 KPI 导向"
        if company.management_score >= 15:
            return "不确定", "管理层评分较高，但缺少创始人式领导者的直接证据", "管理层执行力或利益一致性转弱"
        return "不通过", "管理层评分不足，未体现 owner-operator 特征", "频繁换帅、资本配置失误、过度举债"

    def _assess_core_position(self, industry: IndustryAnalysis) -> tuple[str, str, str]:
        """评估是否处在大趋势核心位置"""
        if industry.total_score >= 75 and industry.market_structure_score >= 18 and industry.entry_barrier_score >= 18:
            return "通过", "行业评分、竞争格局和进入壁垒均较强", "行业需求转弱或公司从核心环节边缘化"
        if industry.total_score >= 60:
            return "不确定", "行业有优势，但是否处于趋势核心仍需和产业链位置交叉验证", "只是概念相关或议价能力弱"
        return "不通过", "行业评分不足，暂不能证明站在大趋势核心", "需求下行、格局恶化、价格战"

    def _assess_quality_flywheel(self, company: CompanyAnalysis) -> tuple[str, str, str]:
        """评估底层规律的飞轮"""
        triple = company.moat_triple_test
        if triple and triple.all_passed:
            return "通过", f"护城河三重检验{triple.conclusion}", "飞轮断裂或优势无法继续自我强化"
        if triple:
            return triple.conclusion, f"护城河三重检验{triple.conclusion}", "底层规律、结构或飞轮任一项被证伪"
        return "不确定", "缺少护城河三重检验结构化证据", "优势只来自短期情绪、周期或单一卖点"

    def _assess_why_now(
        self,
        valuation: ValuationAnalysis,
        contrarian: ContrarianAnalysis,
    ) -> tuple[str, str, str]:
        """评估未来 1-2 年兑现窗口"""
        if valuation.total_score >= 70 and contrarian.price_ok:
            return "通过", "估值与价格条件较友好，具备建立观察窗口的基础", "估值修复后基本面未兑现"
        if valuation.total_score >= 50:
            return "不确定", "估值不算极端，但需要明确 1-2 年催化剂", "催化剂落空或兑现延后"
        return "不通过", "估值安全边际不足，缺少现在出手的理由", "估值继续扩张但基本面没有同步改善"

    def _assess_narrative_premium_risk(self, report: ValueInvestmentReport) -> str:
        """评估叙事溢价削减风险"""
        high_valuation = report.valuation_analysis.historical_percentile_score <= 7.5
        weak_moat = report.company_analysis.moat_score < 20
        weak_profitability = report.company_analysis.profitability_score < 10

        if high_valuation and (weak_moat or weak_profitability):
            return "高"
        if high_valuation or weak_moat:
            return "中"
        return "低"

    def _get_conclusion(
        self,
        final_score: float,
        veto_triggered: bool
    ) -> InvestmentConclusion:
        """确定投资结论"""
        if veto_triggered:
            return InvestmentConclusion.AVOID

        if final_score >= 80:
            return InvestmentConclusion.STRONG_BUY
        elif final_score >= 70:
            return InvestmentConclusion.BUY
        elif final_score >= 60:
            return InvestmentConclusion.WATCH
        elif final_score >= 50:
            return InvestmentConclusion.HOLD
        elif final_score >= 40:
            return InvestmentConclusion.AVOID
        else:
            return InvestmentConclusion.STAY_AWAY

    def _generate_recommendation(
        self,
        conclusion: InvestmentConclusion,
        industry: IndustryAnalysis,
        company: CompanyAnalysis,
        valuation: ValuationAnalysis,
        contrarian: ContrarianAnalysis
    ) -> str:
        """生成投资建议"""
        if conclusion == InvestmentConclusion.STRONG_BUY:
            return f"✅ 好行业、好公司、好价格，符合价值投资标准。{industry.rating.value}+{company.rating.value}+合理估值，可重仓配置。"
        elif conclusion == InvestmentConclusion.BUY:
            return "🟢 整体不错，适合标准仓位买入。三好原则基本满足，长期持有胜率较高。"
        elif conclusion == InvestmentConclusion.WATCH:
            return "🟡 有优点但也有短板，可小仓位试探或等待更好价格。建议继续跟踪。"
        elif conclusion == InvestmentConclusion.HOLD:
            return "🟠 短板较明显，建议继续跟踪但暂不建仓。等待更好的时机。"
        elif conclusion == InvestmentConclusion.AVOID:
            return "🔴 问题较多，不符合价值投资标准。建议远离。"
        else:
            return "❌ 不符合任何价值投资条件。远离。"

    def _suggest_price_range(
        self,
        current_price: float,
        valuation_score: float,
        conclusion: InvestmentConclusion
    ) -> Optional[tuple[float, float]]:
        """建议买入价格区间"""
        if conclusion in [InvestmentConclusion.AVOID, InvestmentConclusion.STAY_AWAY]:
            return None

        # 根据估值分数调整折扣
        if valuation_score >= 75:
            discount = 0.95  # 估值很好，接近当前价
        elif valuation_score >= 60:
            discount = 0.90  # 估值合理，打9折
        else:
            discount = 0.85  # 估值偏高，打85折

        low = current_price * discount * 0.95
        high = current_price * discount * 1.05

        return (low, high)

    def _suggest_position_size(
        self,
        conclusion: InvestmentConclusion,
        final_score: float
    ) -> Optional[str]:
        """建议仓位"""
        if conclusion == InvestmentConclusion.STRONG_BUY:
            return "20-30%（重仓）"
        elif conclusion == InvestmentConclusion.BUY:
            return "10-20%（标准仓位）"
        elif conclusion == InvestmentConclusion.WATCH:
            return "5-10%（小仓试探）"
        else:
            return None

    def _identify_max_risk(
        self,
        industry: IndustryAnalysis,
        company: CompanyAnalysis,
        valuation: ValuationAnalysis
    ) -> str:
        """识别最大风险"""
        risks = []

        if industry.total_score < 50:
            risks.append("行业衰退风险")

        if company.moat_score < 20:
            risks.append("竞争优势丧失风险")

        if valuation.is_value_trap:
            risks.append("估值陷阱风险")

        if company.debt_ratio and company.debt_ratio > 0.7:
            risks.append("财务风险")

        return "、".join(risks) if risks else "整体风险可控"
