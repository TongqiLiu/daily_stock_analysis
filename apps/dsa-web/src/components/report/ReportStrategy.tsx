import type React from 'react';
import type { ReportLanguage, ReportStrategy as ReportStrategyType } from '../../types/analysis';
import { Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';

interface ReportStrategyProps {
  strategy?: ReportStrategyType;
  language?: ReportLanguage;
}

interface StrategyItemProps {
  label: string;
  value?: string;
  tone: string;
}

const StrategyItem: React.FC<StrategyItemProps> = ({
  label,
  value,
  tone,
}) => (
  <div className="home-subpanel home-strategy-card p-3" style={{ ['--home-strategy-tone' as string]: `var(${tone})` }}>
    <div className="flex flex-col">
      <span className="home-strategy-label mb-0.5 text-xs">{label}</span>
      <span className="home-strategy-value text-lg font-bold font-mono" style={!value ? { color: 'var(--text-muted-text)' } : undefined}>
        {value || '—'}
      </span>
    </div>
    <div
      className="absolute bottom-0 left-0 right-0 h-0.5"
      style={{ background: `linear-gradient(90deg, transparent, var(${tone}), transparent)` }}
    />
  </div>
);

interface FearGreedItemProps {
  label: string;
  score?: number;
  scoreLabel?: string;
}

/** 贪恐指数卡片，分值正负决定颜色（红=恐慌，青=贪婪，紫=中性） */
const FearGreedItem: React.FC<FearGreedItemProps> = ({ label, score, scoreLabel }) => {
  const tone =
    score === undefined || score === null
      ? '--home-strategy-secondary'
      : score >= 20
      ? '--home-strategy-buy'    // 贪婪 → 青
      : score <= -20
      ? '--home-strategy-stop'   // 恐慌 → 红
      : '--home-strategy-take';  // 中性 → 黄/紫

  const display =
    score !== undefined && score !== null
      ? `${score > 0 ? '+' : ''}${score}${scoreLabel ? `  ${scoreLabel}` : ''}`
      : undefined;

  return (
    <div className="home-subpanel home-strategy-card p-3" style={{ ['--home-strategy-tone' as string]: `var(${tone})` }}>
      <div className="flex flex-col">
        <span className="home-strategy-label mb-0.5 text-xs">{label}</span>
        <span
          className="home-strategy-value text-lg font-bold font-mono"
          style={!display ? { color: 'var(--text-muted-text)' } : undefined}
        >
          {display || '—'}
        </span>
      </div>
      <div
        className="absolute bottom-0 left-0 right-0 h-0.5"
        style={{ background: `linear-gradient(90deg, transparent, var(${tone}), transparent)` }}
      />
    </div>
  );
};

/**
 * 策略点位区组件 - 终端风格
 */
export const ReportStrategy: React.FC<ReportStrategyProps> = ({ strategy, language = 'zh' }) => {
  if (!strategy) {
    return null;
  }

  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);

  const strategyItems = [
    {
      label: text.idealBuy,
      value: strategy.idealBuy,
      tone: '--home-strategy-buy',
    },
    {
      label: text.secondaryBuy,
      value: strategy.secondaryBuy,
      tone: '--home-strategy-secondary',
    },
    {
      label: text.stopLoss,
      value: strategy.stopLoss,
      tone: '--home-strategy-stop',
    },
    {
      label: text.takeProfit,
      value: strategy.takeProfit,
      tone: '--home-strategy-take',
    },
  ];

  const hasFearGreed =
    strategy.fearGreedScore !== undefined && strategy.fearGreedScore !== null;

  return (
    <Card variant="bordered" padding="md" className="home-panel-card">
      <DashboardPanelHeader
        eyebrow={text.strategyPoints}
        title={text.sniperLevels}
        className="mb-3"
      />
      <div className={`grid gap-3 ${hasFearGreed ? 'grid-cols-2 md:grid-cols-5' : 'grid-cols-2 md:grid-cols-4'}`}>
        {strategyItems.map((item) => (
          <StrategyItem key={item.label} {...item} />
        ))}
        {hasFearGreed && (
          <FearGreedItem
            label={text.fearGreedIndex}
            score={strategy.fearGreedScore}
            scoreLabel={strategy.fearGreedLabel}
          />
        )}
      </div>
    </Card>
  );
};
