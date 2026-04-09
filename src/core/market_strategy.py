# -*- coding: utf-8 -*-
"""Market strategy blueprints for CN/US daily market recap."""

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class StrategyDimension:
    """Single strategy dimension used by market recap prompts."""

    name: str
    objective: str
    checkpoints: List[str]


@dataclass(frozen=True)
class MarketStrategyBlueprint:
    """Region specific market strategy blueprint."""

    region: str
    title: str
    positioning: str
    principles: List[str]
    dimensions: List[StrategyDimension]
    action_framework: List[str]

    def to_prompt_block(self) -> str:
        """Render blueprint as prompt instructions."""
        principles_text = "\n".join([f"- {item}" for item in self.principles])
        action_text = "\n".join([f"- {item}" for item in self.action_framework])

        dims = []
        for dim in self.dimensions:
            checkpoints = "\n".join([f"  - {cp}" for cp in dim.checkpoints])
            dims.append(f"- {dim.name}: {dim.objective}\n{checkpoints}")
        dimensions_text = "\n".join(dims)

        return (
            f"## Strategy Blueprint: {self.title}\n"
            f"{self.positioning}\n\n"
            f"### Strategy Principles\n{principles_text}\n\n"
            f"### Analysis Dimensions\n{dimensions_text}\n\n"
            f"### Action Framework\n{action_text}"
        )

    def to_markdown_block(self) -> str:
        """Render blueprint as markdown section for template fallback report."""
        dims = "\n".join([f"- **{dim.name}**: {dim.objective}" for dim in self.dimensions])
        section_title = "### 六、策略框架"
        return f"{section_title}\n{dims}\n"


CN_BLUEPRINT = MarketStrategyBlueprint(
    region="cn",
    title="A股市场三段式复盘策略",
    positioning="聚焦指数趋势、资金博弈与板块轮动，形成次日交易计划。",
    principles=[
        "先看指数方向，再看量能结构，最后看板块持续性。",
        "结论必须映射到仓位、节奏与风险控制动作。",
        "判断使用当日数据与近3日新闻，不臆测未验证信息。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断市场处于上升、震荡还是防守阶段。",
            checkpoints=["上证/深证/创业板是否同向", "放量上涨或缩量下跌是否成立", "关键支撑阻力是否被突破"],
        ),
        StrategyDimension(
            name="资金情绪",
            objective="识别短线风险偏好与情绪温度。",
            checkpoints=["涨跌家数与涨跌停结构", "成交额是否扩张", "高位股是否出现分歧"],
        ),
        StrategyDimension(
            name="主线板块",
            objective="提炼可交易主线与规避方向。",
            checkpoints=["领涨板块是否具备事件催化", "板块内部是否有龙头带动", "领跌板块是否扩散"],
        ),
    ],
    action_framework=[
        "进攻：指数共振上行 + 成交额放大 + 主线强化。",
        "均衡：指数分化或缩量震荡，控制仓位并等待确认。",
        "防守：指数转弱 + 领跌扩散，优先风控与减仓。",
    ],
)

US_BLUEPRINT = MarketStrategyBlueprint(
    region="us",
    title="美股市场复盘策略框架",
    positioning="围绕美股指数趋势、宏观叙事与板块轮动，形成下一交易时段的风险姿态。",
    principles=[
        "先判断标普 500、纳斯达克、道指是否同向，再区分 Beta 与主题驱动的 Alpha。",
        "结论必须落到风险偏好（进攻/均衡/防守）、仓位与失效条件，避免空泛点评。",
        "仅使用当日数据与近几日新闻，不臆测未验证信息。",
    ],
    dimensions=[
        StrategyDimension(
            name="趋势结构",
            objective="判断市场处于趋势、震荡还是风险偏好下行阶段。",
            checkpoints=[
                "SPX/NDX/DJI 是否同向",
                "量能是否印证方向",
                "关键指数位是否守住或失守",
            ],
        ),
        StrategyDimension(
            name="宏观与资金流向",
            objective="把利率、美元与政策叙事映射到权益风险偏好。",
            checkpoints=[
                "美债收益率与美元对相关资产的含义",
                "广度与龙头集中度",
                "防御 vs 成长风格轮动",
            ],
        ),
        StrategyDimension(
            name="板块与主题",
            objective="识别持续领涨方向与脆弱领跌方向。",
            checkpoints=[
                "AI/半导体/软件等主题是否延续",
                "能源、金融等对宏观数据的敏感度",
                "VIX 与大型科技股财报等波动信号",
            ],
        ),
    ],
    action_framework=[
        "风险偏好上升：主要指数共振上行且参与度扩大。",
        "均衡：指数信号分化，以选择性相对强度为主，控制仓位。",
        "风险偏好下行：假突破或波动抬升，优先保本与减仓。",
    ],
)


def get_market_strategy_blueprint(region: str) -> MarketStrategyBlueprint:
    """Return strategy blueprint by market region."""
    return US_BLUEPRINT if region == "us" else CN_BLUEPRINT
