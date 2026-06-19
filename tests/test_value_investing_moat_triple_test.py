import asyncio

from src.services.value_analysis.company_analyzer import (
    CompanyAnalysis,
    CompanyAnalyzer,
    CompanyRating,
    MoatTripleTest,
    MoatType,
)
from src.services.value_analysis.contrarian_checker import ContrarianAnalysis, MarketSentiment
from src.services.value_analysis.industry_analyzer import IndustryAnalysis, IndustryRating
from src.services.value_analysis.pricing_power_analyzer import PricingPowerAnalysis, PricingPowerLevel
from src.services.value_analysis.valuation_analyzer import ValuationAnalysis
from src.services.value_analysis.value_report_generator import InvestmentConclusion, ValueReportGenerator


class FakeMoatLlm:
    def __init__(self):
        self.prompt = ""

    async def chat(self, prompt: str) -> str:
        self.prompt = prompt
        return """护城河类型: [品牌护城河, 成本护城河]
底层规律: 通过 - 用户复购建立在消费习惯和规模经济上
结构: 通过 - 品牌、渠道和成本优势互相咬合
飞轮: 不通过 - 数据或规模反馈尚未形成自我强化
定价权结论: 不通过 - 三关未全过，不能直接认定强定价权
评分: 38
分析: 品牌和成本优势存在，但飞轮证据不足。"""


def test_company_analyzer_parses_moat_triple_test_and_caps_score():
    llm = FakeMoatLlm()
    analyzer = CompanyAnalyzer(llm)

    score, moat_types, analysis, triple_test = asyncio.run(
        analyzer._analyze_moat("测试公司", "消费", {}, {})
    )

    assert "护城河三重检验" in llm.prompt
    assert score == 30
    assert moat_types == [MoatType.BRAND, MoatType.COST]
    assert analysis == "品牌和成本优势存在，但飞轮证据不足。"
    assert triple_test is not None
    assert triple_test.fundamental_law_status == "通过"
    assert triple_test.structure_status == "通过"
    assert triple_test.flywheel_status == "不通过"
    assert triple_test.pricing_power_status == "不通过"
    assert triple_test.conclusion == "部分通过"


def test_value_report_marks_failed_moat_triple_test_as_veto():
    triple_test = MoatTripleTest(
        fundamental_law_status="通过",
        structure_status="通过",
        flywheel_status="不通过",
        pricing_power_status="不通过",
        fundamental_law_analysis="需求来自稳定消费习惯",
        structure_analysis="品牌、渠道和规模互相咬合",
        flywheel_analysis="缺少越转越快的自我强化证据",
        pricing_power_analysis="三关未全过，强定价权仍待验证",
    )
    company = CompanyAnalysis(
        moat_score=30,
        profitability_score=18,
        financial_health_score=18,
        management_score=18,
        total_score=84,
        rating=CompanyRating.A,
        moat_types=[MoatType.BRAND, MoatType.COST],
        moat_analysis="护城河较强但飞轮不足",
        profitability_analysis="盈利能力优秀",
        financial_health_analysis="财务健康",
        management_analysis="管理层稳健",
        key_findings="",
        risks="",
        gross_margin=0.6,
        roe=0.25,
        net_margin=0.2,
        debt_ratio=0.2,
        moat_triple_test=triple_test,
    )
    industry = IndustryAnalysis(
        market_structure_score=22,
        pricing_power_score=20,
        demand_stability_score=22,
        entry_barrier_score=20,
        total_score=84,
        rating=IndustryRating.A,
        market_structure_analysis="寡头格局",
        pricing_power_analysis="有一定定价权",
        demand_stability_analysis="需求稳定",
        entry_barrier_analysis="壁垒较高",
        key_findings="行业优质",
        risks="无明显风险",
    )
    valuation = ValuationAnalysis(
        pe_score=20,
        supplementary_score=18,
        historical_percentile_score=20,
        safety_margin_score=18,
        total_score=76,
        pe_analysis="PE合理",
        supplementary_analysis="PB合理",
        historical_analysis="历史分位偏低",
        safety_margin_analysis="安全边际一般",
        is_value_trap=False,
        trap_warnings="无明显估值陷阱",
    )
    contrarian = ContrarianAnalysis(
        sentiment=MarketSentiment.NEUTRAL,
        industry_ok=True,
        company_ok=True,
        price_ok=True,
        should_contrarian_buy=False,
        adjustment=0,
        analysis="正常分析",
    )
    pricing_power = PricingPowerAnalysis(
        level=PricingPowerLevel.MODERATE,
        adjustment=0.05,
        sources=["品牌忠诚度"],
        analysis="三重检验未全过，不能直接认定强定价权。",
    )

    report = ValueReportGenerator().generate(
        symbol="TEST",
        company_name="测试公司",
        industry_name="消费",
        industry_analysis=industry,
        company_analysis=company,
        valuation_analysis=valuation,
        contrarian_analysis=contrarian,
        pricing_power_analysis=pricing_power,
        market_data={"current_price": 10, "market_cap": 1000000000},
    )
    markdown = ValueReportGenerator().format_markdown(report)

    assert report.veto_triggered is True
    assert report.conclusion == InvestmentConclusion.AVOID
    assert "护城河三重检验未全过（定价权证据不足）" in report.veto_reasons
    assert "### 护城河三重检验" in markdown
    assert "结构不是一个点" in markdown
    assert "三关结论：部分通过" in markdown
    assert "不能直接认定强定价权" in markdown
    assert "## 🧭 六、价值选股加分项" in markdown
    assert "Founder-led / 创始人式领导者" in markdown
    assert "核心位置" in markdown
    assert "为什么是现在" in markdown
    assert "五件事备忘" in markdown
    assert "## 🚪 七、退出纪律" in markdown
    assert "叙事溢价削减" in markdown
    assert "错了知道怎么走" in markdown
