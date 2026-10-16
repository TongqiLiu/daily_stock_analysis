import { useEffect, useState } from 'react';
import { decisionSignalsApi } from '../../api/decisionSignals';
import type { DecisionSignalItem } from '../../types/decisionSignals';
import { Card } from '../common';
import { SwingTradePlanDetails } from './DecisionSignalDisplay';
import { formatDateTime } from '../../utils/format';

/** Pending plans are visible before direction outcomes mature. No new write API. */
export function RecentSwingPlans() {
  const [items, setItems] = useState<DecisionSignalItem[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    decisionSignalsApi.list({ sourceType: 'agent', page: 1, pageSize: 10 }).then((response) => {
      if (active) { setItems(response.items); setState('ready'); }
    }).catch(() => { if (active) setState('error'); });
    return () => { active = false; };
  }, [revision]);
  return <Card title="最新问股计划 · 含未到期记录" padding="md">
    <p className="mb-3 text-xs text-secondary-text">最近10条问股记录；展开查看当轮保存的不可回溯计划。候选、观望和未成交不算交易胜率。</p>
    <button className="btn-secondary mb-3" type="button" disabled={state === 'loading'} onClick={() => { setState('loading'); setRevision((n) => n + 1); }}>刷新计划记录</button>
    {state === 'loading' ? <p>加载计划中…</p> : state === 'error' ? <p role="alert" className="text-danger">计划读取失败，请重试；不代表没有记录。</p> : items.length === 0 ? <p className="text-secondary-text">尚无问股记录。</p> : (
      <div className="space-y-3">{items.map((item) => <details key={item.id} className="rounded-xl border border-subtle p-3">
        <summary className="cursor-pointer text-sm font-semibold">{item.stockCode} · {formatDateTime(item.createdAt)} · {item.actionLabel || item.action}</summary>
        <div className="mt-3"><p className="mb-2 break-words text-sm">{item.reason}</p><SwingTradePlanDetails metadata={item.metadata} /></div>
      </details>)}</div>
    )}
  </Card>;
}

export function OutcomePlanSnapshot({ signalId, open }: { signalId: number; open: boolean }) {
  return open ? <LoadedPlanSnapshot key={signalId} signalId={signalId} /> : null;
}

function LoadedPlanSnapshot({ signalId }: { signalId: number }) {
  const [item, setItem] = useState<DecisionSignalItem | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    decisionSignalsApi.get(signalId).then((response) => { if (active) setItem(response); })
      .catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [signalId]);
  if (failed) return <p role="alert" className="text-danger">原计划读取失败，关闭后重新展开可重试。</p>;
  if (!item) return <p>读取原计划…</p>;
  return <SwingTradePlanDetails metadata={item.metadata} />;
}
