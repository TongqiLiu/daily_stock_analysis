import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart3, CheckCircle2, Clock3, RefreshCw, ShieldAlert, Target } from 'lucide-react';
import { decisionSignalsApi } from '../api/decisionSignals';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Card, EmptyState, Input, PageHeader, Select, StatCard } from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { DecisionSignalHorizon, DecisionSignalOutcomeEvalStatus, DecisionSignalOutcomeItem, DecisionSignalOutcomeListResponse, DecisionSignalOutcomeStatsBucket, DecisionSignalOutcomeStatsResponse, DecisionSignalOutcomeValue, DecisionSignalPostReviewResponse } from '../types/decisionSignals';
import { getDecisionActionLabel } from '../utils/decisionAction';
import { formatDateTime } from '../utils/format';

type BreakdownKey = 'horizon' | 'action' | 'market_phase' | 'source_type' | 'data_quality_level';
type PerformanceScope = 'all' | 'agent';

const BREAKDOWNS: Array<{ key: BreakdownKey; title: string; description: string }> = [
  { key: 'horizon', title: '按回测周期', description: '比较 T+1、T+3、T+5、T+10 的胜率衰减。' },
  { key: 'action', title: '按问股动作', description: '识别买入、持有、减仓等结论是否存在系统性偏差。' },
  { key: 'market_phase', title: '按市场阶段', description: '比较盘前、盘中和盘后分析的可验证性。' },
  { key: 'source_type', title: '按来源', description: '分开观察问股、报告和告警，避免混淆口径。' },
  { key: 'data_quality_level', title: '按数据质量', description: '确认数据缺口是否正在拖累问股结论。' },
];

const HORIZON_ORDER: Record<string, number> = { '1d': 1, '3d': 3, '5d': 5, '10d': 10 };

function formatPct(value?: number | null): string {
  return value == null ? '—' : `${value.toFixed(1)}%`;
}

function formatPrice(value?: number | null): string {
  return value == null ? '—' : value.toFixed(2);
}

function outcomeLabel(row: DecisionSignalOutcomeItem): string {
  if (row.evalStatus === 'unable') return '无法结算';
  return ({ hit: '命中', miss: '未命中', neutral: '中性' } as Record<string, string>)[row.outcome ?? ''] ?? '待结算';
}

function outcomeTone(row: DecisionSignalOutcomeItem): string {
  if (row.evalStatus === 'unable') return 'text-warning';
  if (row.outcome === 'hit') return 'text-success';
  if (row.outcome === 'miss') return 'text-danger';
  return 'text-secondary-text';
}

function adverseExcursion(row: DecisionSignalOutcomeItem): number | null {
  if (!row.startPrice || row.startPrice <= 0) return null;
  if (['buy', 'add', 'hold', 'watch', 'alert'].includes(row.action ?? '') && row.minLow != null) {
    return Math.max(0, (row.startPrice - row.minLow) / row.startPrice * 100);
  }
  if (['sell', 'reduce', 'avoid'].includes(row.action ?? '') && row.maxHigh != null) {
    return Math.max(0, (row.maxHigh - row.startPrice) / row.startPrice * 100);
  }
  return null;
}

function labelForBucket(key: BreakdownKey, bucket: DecisionSignalOutcomeStatsBucket, language: 'zh' | 'en'): string {
  if (key === 'horizon') {
    const labels: Record<string, string> = language === 'zh'
      ? { '1d': 'T+1', '3d': 'T+3', '5d': 'T+5', '10d': 'T+10' }
      : { '1d': 'T+1', '3d': 'T+3', '5d': 'T+5', '10d': 'T+10' };
    return labels[bucket.value] ?? bucket.value;
  }
  if (key === 'action') return getDecisionActionLabel(bucket.value as never, language) || bucket.value;
  if (key === 'market_phase') {
    const labels: Record<string, string> = language === 'zh'
      ? { premarket: '盘前', intraday: '盘中', postmarket: '盘后', non_trading: '非交易时段', unknown: '未知' }
      : { premarket: 'Premarket', intraday: 'Intraday', postmarket: 'Postmarket', non_trading: 'Non-trading', unknown: 'Unknown' };
    return labels[bucket.value] ?? bucket.value;
  }
  return bucket.value || 'unknown';
}

const DecisionPerformancePage: React.FC = () => {
  const { language } = useUiLanguage();
  const [stats, setStats] = useState<DecisionSignalOutcomeStatsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [settling, setSettling] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [settleMessage, setSettleMessage] = useState<string | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [postReview, setPostReview] = useState<DecisionSignalPostReviewResponse | null>(null);
  const [scope, setScope] = useState<PerformanceScope>('all');
  const [outcomes, setOutcomes] = useState<DecisionSignalOutcomeListResponse | null>(null);
  const [outcomesLoading, setOutcomesLoading] = useState(true);
  const [outcomePage, setOutcomePage] = useState(1);
  const [stockCode, setStockCode] = useState('');
  const [outcomeHorizon, setOutcomeHorizon] = useState<DecisionSignalHorizon | ''>('');
  const [outcomeStatus, setOutcomeStatus] = useState<DecisionSignalOutcomeEvalStatus | ''>('');
  const [outcomeValue, setOutcomeValue] = useState<DecisionSignalOutcomeValue | ''>('');
  const outcomeRequestId = useRef(0);

  const loadOutcomeDetails = useCallback(async () => {
    const requestId = ++outcomeRequestId.current;
    setOutcomesLoading(true);
    try {
      const result = await decisionSignalsApi.listOutcomes({
        stockCode: stockCode.trim() || undefined,
        horizon: outcomeHorizon || undefined,
        evalStatus: outcomeStatus || undefined,
        outcome: outcomeValue || undefined,
        sourceType: scope === 'agent' ? 'agent' : undefined,
        page: outcomePage,
        pageSize: 25,
      });
      if (requestId === outcomeRequestId.current) setOutcomes(result);
    } catch (err) {
      if (requestId === outcomeRequestId.current) setError(getParsedApiError(err));
    } finally {
      if (requestId === outcomeRequestId.current) setOutcomesLoading(false);
    }
  }, [outcomeHorizon, outcomePage, outcomeStatus, outcomeValue, scope, stockCode]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPostReview(null);
    try {
      setStats(await decisionSignalsApi.getOutcomeStats(scope === 'agent' ? { sourceType: 'agent' } : {}));
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setLoading(false);
    }
  }, [scope]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { void loadOutcomeDetails(); }, [loadOutcomeDetails]);

  const settle = async () => {
    setSettling(true);
    setError(null);
    setSettleMessage(null);
    try {
      const result = await decisionSignalsApi.runOutcomes({
        horizons: ['1d', '3d', '5d', '10d'],
        sourceType: scope === 'agent' ? 'agent' : undefined,
        limit: 500,
      });
      setSettleMessage(`本次结算：新增 ${result.created}，更新 ${result.updated}，跳过 ${result.skipped}。`);
      await load();
      await loadOutcomeDetails();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setSettling(false);
    }
  };

  const coverage = useMemo(() => {
    if (!stats?.total) return null;
    return stats.completed / stats.total * 100;
  }, [stats]);

  const generatePostReview = async () => {
    setReviewing(true);
    setError(null);
    setPostReview(null);
    try {
      const result = await decisionSignalsApi.generatePostReview(
        ['1d', '3d', '5d', '10d'],
        scope === 'agent' ? 'agent' : undefined,
      );
      setPostReview(result);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setReviewing(false);
    }
  };

  return (
    <AppPage className="space-y-5">
      <PageHeader
        eyebrow="ASK QUALITY"
        title="问股决策表现中心"
        description="汇总结构化 DecisionSignal 的确定性后验结果；可切换到“仅问股”隔离对话信号，不混入传统报告文本回测。"
        actions={(
          <>
            <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => void load()} disabled={loading}>
              <RefreshCw className="h-4 w-4" /> 刷新
            </button>
            <button type="button" className="btn-primary inline-flex items-center gap-2" onClick={() => void settle()} disabled={settling}>
              <Clock3 className="h-4 w-4" /> {settling ? '结算中…' : '结算待成熟样本'}
            </button>
            <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => void generatePostReview()} disabled={reviewing || !stats?.completed}>
              <Target className="h-4 w-4" /> {reviewing ? '复盘中…' : '生成 AI 后置复盘'}
            </button>
          </>
        )}
      />

      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-subtle bg-card px-4 py-3">
        <span className="mr-1 text-sm font-medium text-secondary-text">统计范围</span>
        {([
          ['all', '全部结构化信号'],
          ['agent', '仅问股对话'],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={scope === value}
            className={scope === value ? 'btn-primary' : 'btn-secondary'}
            onClick={() => {
              setScope(value);
              setPostReview(null);
              setSettleMessage(null);
            }}
          >
            {label}
          </button>
        ))}
        <span className="text-xs text-secondary-text">
          {scope === 'agent' ? '仅 source_type=agent，可直接衡量问股输出。' : '用于历史基线和跨来源比较。'}
        </span>
      </div>

      {error ? <ApiErrorAlert error={error} actionLabel="重试" onAction={() => void load()} /> : null}
      {settleMessage ? <p className="rounded-xl border border-success/20 bg-success/5 px-4 py-3 text-sm text-success">{settleMessage}</p> : null}

      {loading ? (
        <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">{Array.from({ length: 6 }).map((_, index) => <div key={index} className="h-32 animate-pulse rounded-2xl bg-elevated/60" />)}</div>
      ) : !stats || stats.total === 0 ? (
        <EmptyState
          icon={<Target className="h-7 w-7" />}
          title="暂无可统计的问股结果"
          description="先沉淀结构化问股信号；在未来交易日数据充足后，点击“结算待成熟样本”生成胜率和拆分结果。"
        />
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-4 xl:grid-cols-8">
            <StatCard label="结果记录" value={stats.total} hint={`引擎 ${stats.engineVersion}`} icon={<BarChart3 className="h-5 w-5" />} />
            <StatCard label="已结算" value={stats.completed} hint={`覆盖率 ${formatPct(coverage)}`} tone="primary" icon={<CheckCircle2 className="h-5 w-5" />} />
            <StatCard label="方向胜率" value={formatPct(stats.hitRatePct)} hint={`命中 ${stats.hit} / 未命中 ${stats.miss}`} tone={stats.hitRatePct != null && stats.hitRatePct >= 50 ? 'success' : 'danger'} icon={<Target className="h-5 w-5" />} />
            <StatCard label="平均区间涨跌" value={formatPct(stats.avgStockReturnPct)} hint="标的收益，不代表组合收益" tone="primary" />
            <StatCard label="平均不利波动" value={formatPct(stats.avgAdverseExcursionPct)} hint="按建议方向计算 MAE" tone="warning" />
            <StatCard label="最大不利波动" value={formatPct(stats.maxAdverseExcursionPct)} hint="样本内最坏回撤" tone="danger" />
            <StatCard label="中性样本" value={stats.neutral} hint="不纳入胜率分母" tone="warning" />
            <StatCard label="无法结算" value={stats.unable} hint="优先排查数据/时点缺口" tone="warning" icon={<ShieldAlert className="h-5 w-5" />} />
          </div>

          <Card title="如何用这张表优化问股" subtitle="先看样本与覆盖，再看胜率；不要直接按单次结果改提示词或策略权重。" padding="md">
            <div className="grid gap-3 text-sm text-secondary-text md:grid-cols-3">
              <p><strong className="text-foreground">1. 样本不足：</strong>先增加可回测信号覆盖，避免凭几次对错优化流程。</p>
              <p><strong className="text-foreground">2. 某类动作低胜率：</strong>检查动作门槛、失效位和数据质量，不直接压低全部评分。</p>
              <p><strong className="text-foreground">3. 无法结算偏高：</strong>优先补交易日、市场阶段和行情数据链路。</p>
            </div>
          </Card>

          {postReview ? (
            <Card
              title="AI 后置复盘"
              subtitle={`只解释确定性统计，不重算胜负、不自动修改权重。样本 ${postReview.completedSamples} · ${postReview.provider || 'unknown'} / ${postReview.model || 'unknown'} · ${formatDateTime(postReview.generatedAt)}`}
              padding="md"
            >
              <div className="chat-prose"><Markdown remarkPlugins={[remarkGfm]}>{postReview.content}</Markdown></div>
            </Card>
          ) : null}

          <div className="grid gap-4 xl:grid-cols-2">
            {BREAKDOWNS.map(({ key, title, description }) => {
              const rows = [...(stats.breakdowns[key] ?? [])].sort((left, right) => (
                key === 'horizon'
                  ? (HORIZON_ORDER[left.value] ?? 99) - (HORIZON_ORDER[right.value] ?? 99)
                  : 0
              ));
              return (
                <Card key={key} title={title} subtitle={description} padding="md">
                  {rows.length === 0 ? <p className="text-sm text-secondary-text">暂无该维度的已结算样本。</p> : (
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-sm">
                        <thead className="border-b border-subtle text-left text-xs text-secondary-text"><tr><th className="pb-2 pr-3">维度</th><th className="pb-2 pr-3 text-right">样本</th><th className="pb-2 pr-3 text-right">已结算</th><th className="pb-2 pr-3 text-right">胜率</th><th className="pb-2 pr-3 text-right">平均涨跌</th><th className="pb-2 pr-3 text-right">平均不利</th><th className="pb-2 text-right">最坏不利</th></tr></thead>
                        <tbody>{rows.slice(0, 8).map((row) => <tr key={row.value} className="border-b border-subtle/70 last:border-0"><td className="py-2.5 pr-3 font-medium text-foreground">{labelForBucket(key, row, language)}</td><td className="py-2.5 pr-3 text-right">{row.total}</td><td className="py-2.5 pr-3 text-right">{row.completed}</td><td className="py-2.5 pr-3 text-right font-medium text-foreground">{formatPct(row.hitRatePct)}</td><td className="py-2.5 pr-3 text-right">{formatPct(row.avgStockReturnPct)}</td><td className="py-2.5 pr-3 text-right">{formatPct(row.avgAdverseExcursionPct)}</td><td className="py-2.5 text-right">{formatPct(row.maxAdverseExcursionPct)}</td></tr>)}</tbody>
                      </table>
                    </div>
                  )}
                </Card>
              );
            })}
          </div>

          <Card
            title="逐笔回测明细"
            subtitle="每行是一条“信号 × 回测周期”记录。展开后可核对原始结论、计划价格、日线区间和无法结算原因；它才是判断胜率是否可信的审计底稿。"
            padding="md"
          >
            <div className="mb-4 grid gap-2 md:grid-cols-2 xl:grid-cols-5">
              <Input
                value={stockCode}
                onChange={(event) => { setStockCode(event.target.value); setOutcomePage(1); }}
                placeholder="筛选代码，例如 NFLX"
                aria-label="筛选股票代码"
              />
              <Select value={outcomeHorizon} onChange={(value) => { setOutcomeHorizon(value as DecisionSignalHorizon | ''); setOutcomePage(1); }} label="回测周期" options={[{ value: '', label: '全部周期' }, { value: '1d', label: 'T+1' }, { value: '3d', label: 'T+3' }, { value: '5d', label: 'T+5' }, { value: '10d', label: 'T+10' }]} />
              <Select value={outcomeStatus} onChange={(value) => { setOutcomeStatus(value as DecisionSignalOutcomeEvalStatus | ''); setOutcomePage(1); }} label="结算状态" options={[{ value: '', label: '全部状态' }, { value: 'completed', label: '已结算' }, { value: 'unable', label: '无法结算' }]} />
              <Select value={outcomeValue} onChange={(value) => { setOutcomeValue(value as DecisionSignalOutcomeValue | ''); setOutcomePage(1); }} label="回测结果" options={[{ value: '', label: '全部结果' }, { value: 'hit', label: '命中' }, { value: 'miss', label: '未命中' }, { value: 'neutral', label: '中性' }]} />
              <button type="button" className="btn-secondary" onClick={() => void loadOutcomeDetails()} disabled={outcomesLoading}>刷新明细</button>
            </div>

            {outcomesLoading ? <div className="h-56 animate-pulse rounded-xl bg-elevated/60" /> : !outcomes?.items.length ? (
              <p className="rounded-xl border border-subtle bg-elevated/30 px-4 py-6 text-sm text-secondary-text">当前筛选条件下没有逐笔结果。新的问股信号需要先产生，并等待对应交易日成熟。</p>
            ) : (
              <>
                <div className="mb-3 flex items-center justify-between text-xs text-secondary-text"><span>共 {outcomes.total} 条，当前第 {outcomes.page} 页</span><span>胜率只由“命中 / 未命中”计算；中性与无法结算不进分母。</span></div>
                <div className="overflow-x-auto rounded-xl border border-subtle">
                  <table className="min-w-[1120px] w-full text-sm">
                    <thead className="border-b border-subtle bg-elevated/40 text-left text-xs text-secondary-text"><tr><th className="px-3 py-2.5">分析时点 / 标的</th><th className="px-3 py-2.5">结论</th><th className="px-3 py-2.5">回测</th><th className="px-3 py-2.5 text-right">起点 → 收盘</th><th className="px-3 py-2.5 text-right">区间涨跌</th><th className="px-3 py-2.5 text-right">不利波动</th><th className="px-3 py-2.5">结果</th><th className="px-3 py-2.5">审计</th></tr></thead>
                    <tbody>{outcomes.items.map((row) => (
                      <tr key={row.id} className="border-b border-subtle/70 align-top last:border-0">
                        <td className="px-3 py-3"><div className="font-semibold text-foreground">{row.stockCode ?? '—'} <span className="font-normal text-secondary-text">{row.stockName ?? ''}</span></div><div className="mt-1 text-xs text-secondary-text">{formatDateTime(row.signalCreatedAt)}</div></td>
                        <td className="px-3 py-3"><div className="font-medium text-foreground">{getDecisionActionLabel(row.action as never, language) || row.action || '—'}</div><div className="mt-1 text-xs text-secondary-text">{labelForBucket('horizon', { value: row.horizon } as DecisionSignalOutcomeStatsBucket, language)} · {row.marketPhase || '未知'}</div></td>
                        <td className="px-3 py-3"><div>锚点 {row.anchorDate || '—'}</div><div className="mt-1 text-xs text-secondary-text">后续 {row.evalWindowDays ?? '—'} 根日线</div></td>
                        <td className="px-3 py-3 text-right font-mono text-xs text-foreground">{formatPrice(row.startPrice)} → {formatPrice(row.endClose)}</td>
                        <td className="px-3 py-3 text-right font-medium text-foreground">{formatPct(row.stockReturnPct)}</td>
                        <td className="px-3 py-3 text-right text-warning">{formatPct(adverseExcursion(row))}</td>
                        <td className={`px-3 py-3 font-semibold ${outcomeTone(row)}`}><div>{outcomeLabel(row)}</div>{row.unableReason ? <div className="mt-1 max-w-40 text-xs font-normal text-secondary-text">{row.unableReason}</div> : null}</td>
                        <td className="px-3 py-3"><details className="group min-w-64"><summary className="cursor-pointer text-xs font-medium text-primary hover:underline">查看原始结论与计划</summary><div className="mt-2 space-y-2 rounded-lg border border-subtle bg-elevated/30 p-2.5 text-xs text-secondary-text"><p className="whitespace-pre-wrap text-foreground">{row.reason || '未保存原始结论。'}</p><p>计划：入场 {formatPrice(row.entryLow)}–{formatPrice(row.entryHigh)} · 止损 {formatPrice(row.stopLoss)} · 目标 {formatPrice(row.targetPrice)}</p><p>日线区间：高 {formatPrice(row.maxHigh)} · 低 {formatPrice(row.minLow)}</p><p>数据质量：{row.dataQualityLevel || 'unknown'} · 信号 ID：{row.signalId}</p></div></details></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <div className="mt-4 flex items-center justify-end gap-2"><button type="button" className="btn-secondary" disabled={outcomes.page <= 1 || outcomesLoading} onClick={() => setOutcomePage((page) => Math.max(1, page - 1))}>上一页</button><button type="button" className="btn-secondary" disabled={outcomesLoading || outcomes.page * outcomes.pageSize >= outcomes.total} onClick={() => setOutcomePage((page) => page + 1)}>下一页</button></div>
              </>
            )}
          </Card>
        </>
      )}
    </AppPage>
  );
};

export default DecisionPerformancePage;
