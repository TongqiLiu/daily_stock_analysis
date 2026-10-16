import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { SwingTradePlanDetails } from '../DecisionSignalDisplay';
import { OutcomePlanSnapshot, RecentSwingPlans } from '../SwingPlanHistory';

const { list, get } = vi.hoisted(() => ({ list: vi.fn(), get: vi.fn() }));
vi.mock('../../../api/decisionSignals', () => ({ decisionSignalsApi: { list, get } }));
const metadata = { trade_plan: { version: 'daily-swing-v1', stock_code: 'ORCL', status: 'validation_pending',
  setup_label: '初次突破', reason: '候选，尚未启用', risk_budget_fraction: .25, max_entry_price: 102,
  hard_stop: 95, structure_low: 96, entry_low: 100, entry_high: 102, new_position_allowed: false,
  signal_date: '2026-10-15', valid_session: '2026-10-16', cancellation_conditions: ['超价取消'],
  event_warning: '事件尚未核验' } };

describe('Daily swing plan history', () => {
  beforeEach(() => {
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
    list.mockReset().mockResolvedValue({ items: [{ id: 1, stockCode: 'ORCL', action: 'watch', metadata }] });
    get.mockReset().mockResolvedValue({ id: 1, metadata });
  });
  it('shows pending plans without waiting for direction settlement', async () => {
    render(<UiLanguageProvider><RecentSwingPlans /></UiLanguageProvider>);
    const summary = await screen.findByText(/ORCL ·/);
    fireEvent.click(summary);
    expect(screen.getByText('日线波段执行计划')).toBeInTheDocument();
    expect(screen.getByText('25%')).toBeInTheDocument();
    expect(screen.getByText('最高买价 · 超过即放弃')).toBeInTheDocument();
    expect(screen.getByText(/超价取消/)).toBeInTheDocument();
    expect(get).not.toHaveBeenCalled();
  });
  it('loads original metadata only when an outcome is expanded', async () => {
    const view = render(<UiLanguageProvider><OutcomePlanSnapshot signalId={1} open={false} /></UiLanguageProvider>);
    expect(get).not.toHaveBeenCalled();
    view.rerender(<UiLanguageProvider><OutcomePlanSnapshot signalId={1} open /></UiLanguageProvider>);
    expect(await screen.findByText('日线波段执行计划')).toBeInTheDocument();
    expect(get).toHaveBeenCalledWith(1);
  });
  it('exposes failed reads and permits retry instead of implying empty history', async () => {
    list.mockRejectedValueOnce(new Error('unavailable'));
    render(<UiLanguageProvider><RecentSwingPlans /></UiLanguageProvider>);
    expect(await screen.findByRole('alert')).toHaveTextContent('读取失败');
    fireEvent.click(screen.getByRole('button', { name: '刷新计划记录' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(await screen.findByText(/ORCL ·/)).toBeInTheDocument();
  });
  it('ignores malformed or legacy metadata and escapes plan strings', () => {
    const view = render(<UiLanguageProvider><SwingTradePlanDetails metadata={{ trade_plan: [] }} /></UiLanguageProvider>);
    expect(screen.queryByText('日线波段执行计划')).not.toBeInTheDocument();
    view.rerender(<UiLanguageProvider><SwingTradePlanDetails metadata={{ trade_plan: { ...metadata.trade_plan, reason: '<script>alert(1)</script>' } }} /></UiLanguageProvider>);
    expect(screen.getByText('<script>alert(1)</script>')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
  });
  it('does not display a reversed range as an executable entry', () => {
    render(<UiLanguageProvider><SwingTradePlanDetails metadata={{ trade_plan: {
      ...metadata.trade_plan, status: 'overpriced', entry_low: 105, entry_high: 102,
    } }} /></UiLanguageProvider>);
    expect(screen.getByText('无可执行区间')).toBeInTheDocument();
    expect(screen.getByText('超价放弃')).toBeInTheDocument();
    expect(screen.queryByText('105 – 102')).not.toBeInTheDocument();
  });
});
