import { useCallback, useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/arc/button/button';
import { Checkbox } from '@/components/arc/checkbox/checkbox';
import { ConfirmMorph } from '@/components/arc/confirm-morph/confirm-morph';
import EmptyState from '@/components/arc/empty-state/empty-state';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { api, type RunSummary } from '@/lib/api';
import page from '@/lib/page.module.css';
import playStyles from '@/pages/play/play.module.css';
import { RunBadge, RunLine, runProgress, runSetup, shortTime } from '@/pages/play/runs';
import { RunViewer } from '@/pages/play/viewer';

import styles from './runs.module.css';

const POLL_MS = 3000;

type DeleteResult = { deleted: string[]; skipped: { id: string; reason: string }[] };

/** 地址里带的对局(对局页「在对局记录中查看」):#/runs?run=<id> */
function linkedRun() {
  return new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('run');
}

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(linkedRun);
  const [picking, setPicking] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [liveAvailable, setLiveAvailable] = useState(false);

  const refresh = useCallback(() => {
    return api<RunSummary[]>('/api/runs').then(setRuns).catch(() => setRuns((prev) => prev ?? []));
  }, []);

  useEffect(() => {
    refresh();
    api<{ available: boolean }>('/api/live').then((r) => setLiveAvailable(r.available)).catch(() => setLiveAvailable(false));
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  // 没选或选中的被删了:选最新一局
  useEffect(() => {
    if (runs?.length && !runs.some((r) => r.id === selected)) setSelected(runs[0].id);
  }, [runs, selected]);

  const run = runs?.find((r) => r.id === selected) ?? null;
  const deletable = useMemo(() => (runs ?? []).filter((r) => !r.live), [runs]);
  const count = deletable.filter((r) => picked.has(r.id)).length;

  const remove = async (ids: string[]) => {
    const result = await api<DeleteResult>('/api/runs/delete', { method: 'POST', body: { ids } });
    setPicked((prev) => new Set([...prev].filter((id) => !result.deleted.includes(id))));
    await refresh();
    if (result.skipped.length) throw new Error(result.skipped.map((s) => s.reason).join(';'));
  };

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const allPicked = deletable.length > 0 && count === deletable.length;

  return (
    <div className={`${page.page} ${page.wide} ${page.fill}`}>
      <header className={styles.header}>
        <div>
          <h1 className={page.title}>对局记录</h1>
          {runs && runs.length > 0 && <p className={page.subtitle}>{runs.length} 局</p>}
        </div>
        {deletable.length > 0 &&
          (picking ? (
            <div className={styles.bulk}>
              <Checkbox label="全选" checked={allPicked ? true : count > 0 ? 'indeterminate' : false}
                onCheckedChange={() => setPicked(allPicked ? new Set() : new Set(deletable.map((r) => r.id)))} />
              {count > 0 && (
                <ConfirmMorph
                  label={`删除 ${count} 局`}
                  prompt={`删除这 ${count} 局记录?`}
                  confirmLabel="删除"
                  cancelLabel="取消"
                  pendingLabel="删除中"
                  doneLabel="已删除"
                  errorLabel="有记录没删掉"
                  retryLabel="重试"
                  onConfirm={() => remove(deletable.filter((r) => picked.has(r.id)).map((r) => r.id))}
                />
              )}
              <Button variant="ghost" onClick={() => { setPicking(false); setPicked(new Set()); }}>
                完成
              </Button>
            </div>
          ) : (
            <Button variant="secondary" onClick={() => setPicking(true)}>
              批量删除
            </Button>
          ))}
      </header>

      {runs === null ? (
        <Skeleton className={playStyles.skeleton} />
      ) : runs.length === 0 ? (
        <EmptyState title="还没有对局记录" description="在对局页开始一局后,每一步的截图和模型判断都会记在这里。" />
      ) : (
        <div className={styles.body}>
          <nav className={styles.list} aria-label="对局列表">
            {runs.map((r) => {
              const pickable = picking && !r.live;
              return (
                <div key={r.id} className={styles.item}>
                  {pickable && (
                    <Checkbox aria-label={`选择 ${shortTime(r.started_at)} 这局`} checked={picked.has(r.id)}
                      onCheckedChange={() => toggle(r.id)} className={styles.pick} />
                  )}
                  <button type="button" className={playStyles.run} aria-current={r.id === selected ? 'true' : undefined}
                    onClick={() => (pickable ? toggle(r.id) : setSelected(r.id))}>
                    <span className={playStyles.runTop}>
                      <span className={playStyles.runTime}>{shortTime(r.started_at)}</span>
                      <RunBadge run={r} />
                    </span>
                    <span className={playStyles.runMeta}>{runSetup(r)}</span>
                    <span className={playStyles.runMeta}>{runProgress(r)}</span>
                  </button>
                </div>
              );
            })}
          </nav>

          {run && (
            <div className={styles.detail}>
              <div className={styles.detailHead}>
                <RunLine run={run} />
                {!run.live && (
                  <ConfirmMorph
                    key={run.id}
                    label="删除"
                    prompt="删除这局记录?"
                    confirmLabel="删除"
                    cancelLabel="取消"
                    pendingLabel="删除中"
                    doneLabel="已删除"
                    errorLabel="删除失败"
                    retryLabel="重试"
                    onConfirm={() => remove([run.id])}
                  />
                )}
              </div>
              <div className={playStyles.frame}>
                <RunViewer key={run.id} run={run} liveAvailable={liveAvailable} />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
