"""
价值分析工具

为 Agent 提供系统化的价值投资分析能力，基于邱国鹭《投资中最简单的事》方法论。
"""

import logging
from typing import Dict, Any, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

logger = logging.getLogger(__name__)

_VALUE_ANALYSIS_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["network_read", "db_read"],
    permissions=["analysis:read"],
    scope_dimensions=["stock"],
)


async def _handle_run_value_analysis(
        stock_code: str,
        include_sections: Optional[str] = "all"
    ) -> Dict[str, Any]:
        """
        执行价值投资分析

        Args:
            stock_code: 股票代码（如 600519、0700.HK、AAPL）
            include_sections: 包含的分析模块，逗号分隔。可选值：
                - "all": 全部分析（默认）
                - "industry": 仅行业分析
                - "company": 仅公司分析
                - "valuation": 仅估值分析
                - "industry,company": 行业+公司分析

        Returns:
            包含以下字段的字典：
            - final_score: 综合评分 (0-100)
            - conclusion: 投资结论（强烈推荐/推荐/可关注/观望/不推荐/远离）
            - industry_analysis: 行业分析结果（行业格局/定价权/需求稳定性/进入壁垒）
            - company_analysis: 公司分析结果（护城河/盈利能力/财务健康/管理层）
            - valuation_analysis: 估值分析结果（PE分析/辅助估值/历史分位/安全边际）
            - contrarian_analysis: 逆向投资检查结果
            - pricing_power_analysis: 定价权分析结果
            - suggested_price_range: 建议买入价格区间
            - suggested_position_size: 建议仓位
            - max_risk: 最大风险
            - report_markdown: Markdown 格式完整报告

        示例：
            >>> result = await run_value_analysis("600519")
            >>> print(f"综合评分: {result['final_score']}")
            >>> print(f"投资结论: {result['conclusion']}")
        """
        try:
            from src.services.value_analysis import (
                DataValidator,
                IndustryAnalyzer,
                CompanyAnalyzer,
                ValuationAnalyzer,
                ContrarianChecker,
                PricingPowerAnalyzer,
                ValueReportGenerator,
            )
            from src.config import get_config
            from data_provider.base import get_realtime_quote, get_financial_data

            config = get_config()

            # 解析包含的分析模块
            sections = set(s.strip().lower() for s in include_sections.split(','))
            if 'all' in sections:
                sections = {'industry', 'company', 'valuation', 'contrarian', 'pricing_power'}

            # 获取基础数据
            logger.info(f"开始价值分析: {stock_code}")

            # 1. 获取数据提供者
            from data_provider import (
                longbridge_fetcher,
                yfinance_fetcher,
                akshare_fetcher,
                futu_fetcher,
            )

            data_providers = {
                'longbridge': longbridge_fetcher,
                'yfinance': yfinance_fetcher,
                'akshare': akshare_fetcher,
                'futu': futu_fetcher,
            }

            # 2. 数据校验
            validator = DataValidator(data_providers)
            price_validation = validator.validate_price(stock_code)

            if not price_validation.is_valid:
                return {
                    "success": False,
                    "error": f"数据校验失败: {price_validation.warning}",
                    "final_score": 0,
                }

            current_price = price_validation.final_value

            # 获取市场数据和财务数据
            market_data = {
                'current_price': current_price,
                'market_cap': 0,  # TODO: 从数据源获取
                'pe_ttm': 0,
                'pb': 0,
                'ps': 0,
            }

            financial_data = {}  # TODO: 从数据源获取

            # 3. 获取公司基本信息
            company_name = stock_code  # TODO: 从数据源获取实际名称
            industry_name = "未知行业"  # TODO: 从数据源获取

            # 4. 执行分析
            from src.services.agent_model_service import AgentModelService

            llm_service = AgentModelService(config)

            results = {}

            # 行业分析
            if 'industry' in sections:
                logger.info("执行行业分析...")
                industry_analyzer = IndustryAnalyzer(llm_service)
                results['industry_analysis'] = await industry_analyzer.analyze(
                    stock_code, industry_name, company_name, market_data
                )

            # 公司分析
            if 'company' in sections:
                logger.info("执行公司分析...")
                company_analyzer = CompanyAnalyzer(llm_service)
                results['company_analysis'] = await company_analyzer.analyze(
                    stock_code, company_name, industry_name, financial_data, market_data
                )

            # 估值分析
            if 'valuation' in sections:
                logger.info("执行估值分析...")
                valuation_analyzer = ValuationAnalyzer(llm_service)
                company_score = results.get('company_analysis', type('obj', (), {'total_score': 50})()).total_score
                results['valuation_analysis'] = await valuation_analyzer.analyze(
                    stock_code, company_name, market_data, financial_data, company_score
                )

            # 逆向投资检查
            if 'contrarian' in sections and 'industry' in results and 'company' in results and 'valuation' in results:
                logger.info("执行逆向投资检查...")
                contrarian_checker = ContrarianChecker(llm_service)
                results['contrarian_analysis'] = await contrarian_checker.check(
                    stock_code,
                    company_name,
                    results['industry_analysis'].total_score,
                    results['company_analysis'].total_score,
                    results['valuation_analysis'].total_score,
                    market_data,
                )

            # 定价权分析
            if 'pricing_power' in sections:
                logger.info("执行定价权分析...")
                pricing_power_analyzer = PricingPowerAnalyzer(llm_service)
                results['pricing_power_analysis'] = await pricing_power_analyzer.analyze(
                    stock_code, company_name, industry_name, market_data, financial_data
                )

            # 5. 生成报告
            if all(k in results for k in ['industry_analysis', 'company_analysis', 'valuation_analysis']):
                logger.info("生成综合报告...")
                generator = ValueReportGenerator()

                report = generator.generate(
                    symbol=stock_code,
                    company_name=company_name,
                    industry_name=industry_name,
                    industry_analysis=results['industry_analysis'],
                    company_analysis=results['company_analysis'],
                    valuation_analysis=results['valuation_analysis'],
                    contrarian_analysis=results.get('contrarian_analysis'),
                    pricing_power_analysis=results.get('pricing_power_analysis'),
                    market_data=market_data,
                    data_validator=validator,
                )

                # 生成 Markdown 报告
                report_markdown = generator.format_markdown(report)
                company_result = results['company_analysis']
                moat_triple_test = company_result.moat_triple_test

                return {
                    "success": True,
                    "stock_code": stock_code,
                    "company_name": company_name,
                    "final_score": report.final_score,
                    "conclusion": report.conclusion.value,
                    "industry_score": report.industry_score,
                    "company_score": report.company_score,
                    "valuation_score": report.valuation_score,
                    "suggested_price_range": report.suggested_price_range,
                    "suggested_position_size": report.suggested_position_size,
                    "max_risk": report.max_risk,
                    "report_markdown": report_markdown,
                    # 详细分析结果
                    "industry_analysis": {
                        "total_score": results['industry_analysis'].total_score,
                        "rating": results['industry_analysis'].rating.value,
                        "key_findings": results['industry_analysis'].key_findings,
                    },
                    "company_analysis": {
                        "total_score": company_result.total_score,
                        "rating": company_result.rating.value,
                        "moat_score": company_result.moat_score,
                        "moat_triple_test": {
                            "fundamental_law_status": moat_triple_test.fundamental_law_status,
                            "structure_status": moat_triple_test.structure_status,
                            "flywheel_status": moat_triple_test.flywheel_status,
                            "pricing_power_status": moat_triple_test.pricing_power_status,
                            "conclusion": moat_triple_test.conclusion,
                        } if moat_triple_test else None,
                        "key_findings": company_result.key_findings,
                    },
                    "valuation_analysis": {
                        "total_score": results['valuation_analysis'].total_score,
                        "is_value_trap": results['valuation_analysis'].is_value_trap,
                    },
                }
            else:
                # 部分分析
                return {
                    "success": True,
                    "stock_code": stock_code,
                    "message": "部分分析完成",
                    **{k: v for k, v in results.items()},
                }

        except Exception as e:
            logger.error(f"价值分析失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "final_score": 0,
            }


# ============================================================
# Tool Definitions
# ============================================================

run_value_analysis_tool = ToolDefinition(
    name="run_value_analysis",
    description=(
        "执行系统化价值投资分析，基于邱国鹭《投资中最简单的事》三好原则（好行业×好公司×好价格）。"
            "返回行业分析、公司分析、估值分析、护城河三重检验、价值选股加分项、退出纪律、逆向投资和定价权评估的综合报告。"
        "相比基础的 get_valuation_percentile，本工具提供更全面的价值投资分析框架。"
    ),
    parameters=[
        ToolParameter(
            name="stock_code",
            type="string",
            description="股票代码，如 600519、0700.HK、AAPL",
            required=True,
        ),
        ToolParameter(
            name="include_sections",
            type="string",
            description=(
                "包含的分析模块，逗号分隔。可选值：all（全部）、industry（行业）、"
                "company（公司）、valuation（估值）、contrarian（逆向）、pricing_power（定价权）"
            ),
            required=False,
            default="all",
        ),
    ],
    handler=_handle_run_value_analysis,
    category="analysis",
    policy=_VALUE_ANALYSIS_READ_POLICY,
)


ALL_VALUE_ANALYSIS_TOOLS = [
    run_value_analysis_tool,
]
