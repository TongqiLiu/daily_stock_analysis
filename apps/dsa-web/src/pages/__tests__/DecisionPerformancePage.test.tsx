import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../contexts/UiLanguageContext';
import DecisionPerformancePage from '../DecisionPerformancePage';

const { mockGetOutcomeStats, mockListOutcomes, mockRunOutcomes, mockGeneratePostReview } = vi.hoisted(() => ({
  mockGetOutcomeStats: vi.fn(),
  mockListOutcomes: vi.fn(),
  mockRunOutcomes: vi.fn(),
  mockGeneratePostReview: vi.fn(),
}));

vi.mock('../../api/decisionSignals', () => ({
  decisionSignalsApi: {
    getOutcomeStats: mockGetOutcomeStats,
    listOutcomes: mockListOutcomes,
    runOutcomes: mockRunOutcomes,
    generatePostReview: mockGeneratePostReview,
  },
}));

const stats = {
  engineVersion: 'decision-signal-v1',
  horizons: null,
  statuses: ['active'],
  sourceType: null,
  total: 18,
  completed: 12,
  unable: 2,
  hit: 7,
  miss: 3,
  neutral: 2,
  hitRatePct: 70,
  avgStockReturnPct: 2.4,
  avgAdverseExcursionPct: 3.6,
  maxAdverseExcursionPct: 9.2,
  unableReasons: {},
  breakdowns: {
    horizon: [],
    action: [{ dimension: 'action', value: 'buy', total: 10, completed: 8, unable: 1, hit: 5, miss: 2, neutral: 1, hitRatePct: 71.4, avgStockReturnPct: 3.2, unableReasons: {} }],
    market_phase: [],
    source_type: [],
    data_quality_level: [],
  },
};

describe('DecisionPerformancePage', () => {
  beforeEach(() => {
    mockGetOutcomeStats.mockReset().mockResolvedValue(stats);
    mockListOutcomes.mockReset().mockResolvedValue({
      total: 1,
      page: 1,
      pageSize: 25,
      items: [{
        id: 12,
        signalId: 7,
        stockCode: 'NFLX',
        stockName: 'Netflix',
        signalCreatedAt: '2026-09-01T08:00:00Z',
        horizon: '3d',
        engineVersion: 'decision-signal-v1',
        evalStatus: 'completed',
        outcome: 'hit',
        anchorDate: '2026-09-01',
        evalWindowDays: 3,
        startPrice: 100,
        endClose: 105,
        maxHigh: 107,
        minLow: 98,
        stockReturnPct: 5,
        action: 'buy',
        market: 'us',
        marketPhase: 'postmarket',
        sourceType: 'analysis',
        holdingState: 'unknown',
        reason: '突破后回踩不破，可小仓试多。',
        entryLow: 99,
        entryHigh: 101,
        stopLoss: 95,
        targetPrice: 110,
      }],
    });
    mockRunOutcomes.mockReset().mockResolvedValue({ items: [], evaluated: 0, created: 2, updated: 1, skipped: 3, engineVersion: 'decision-signal-v1' });
    mockGeneratePostReview.mockReset().mockResolvedValue({
      content: '## 结论\nT+10 胜率衰减。',
      provider: 'test',
      model: 'fixture',
      promptVersion: 'decision-signal-post-review-v1',
      completedSamples: 12,
      generatedAt: '2026-09-05T10:00:00Z',
    });
  });

  it('summarizes structured outcome coverage and win rate independently', async () => {
    render(<UiLanguageProvider><DecisionPerformancePage /></UiLanguageProvider>);

    expect(await screen.findByRole('heading', { name: '问股决策表现中心' })).toBeInTheDocument();
    expect(screen.getByText('70.0%')).toBeInTheDocument();
    expect(screen.getAllByText('买入').length).toBeGreaterThan(0);
    expect(screen.getByText('覆盖率 66.7%')).toBeInTheDocument();
    expect(await screen.findByText('逐笔回测明细')).toBeInTheDocument();
    expect(screen.getByText('NFLX')).toBeInTheDocument();
    expect(screen.getAllByText('命中').length).toBeGreaterThan(0);
  });

  it('runs deterministic outcome settlement before refreshing the dashboard', async () => {
    render(<UiLanguageProvider><DecisionPerformancePage /></UiLanguageProvider>);
    await screen.findByRole('heading', { name: '问股决策表现中心' });

    fireEvent.click(screen.getByRole('button', { name: '结算待成熟样本' }));

    await waitFor(() => expect(mockRunOutcomes).toHaveBeenCalledWith({ horizons: ['1d', '3d', '5d', '10d'], sourceType: undefined, limit: 500 }));
    expect(await screen.findByText('本次结算：新增 2，更新 1，跳过 3。')).toBeInTheDocument();
  });

  it('isolates Ask Stock signals and sends the same scope to AI review', async () => {
    render(<UiLanguageProvider><DecisionPerformancePage /></UiLanguageProvider>);
    await screen.findByRole('heading', { name: '问股决策表现中心' });

    fireEvent.click(screen.getByRole('button', { name: '仅问股对话' }));
    await waitFor(() => expect(mockGetOutcomeStats).toHaveBeenLastCalledWith({ sourceType: 'agent' }));
    fireEvent.click(screen.getByRole('button', { name: '生成 AI 后置复盘' }));

    await waitFor(() => expect(mockGeneratePostReview).toHaveBeenCalledWith(['1d', '3d', '5d', '10d'], 'agent'));
    expect(await screen.findByRole('heading', { name: '结论' })).toBeInTheDocument();
    await waitFor(() => expect(mockListOutcomes).toHaveBeenLastCalledWith(expect.objectContaining({ sourceType: 'agent' })));
  });
});
