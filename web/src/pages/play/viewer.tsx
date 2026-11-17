import { ChevronLeft, ChevronRight, ChevronsRight } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/arc/button/button';
import EmptyState from '@/components/arc/empty-state/empty-state';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { api, enc, type RunSummary, type StepRecord } from '@/lib/api';

import styles from './play.module.css';
import { LiveView } from './live';
import { ShotView, StepDetail, screenName } from './step';

const POLL_MS = 2000;

/** 一局的逐步回看:中间截图(进行中的一局可切到实时画面)和步骤列表,右边这一步的判断。 */
export function RunViewer({ run, liveAvailable }: { run: RunSummary; liveAvailable: boolean }) {
  const [steps, setSteps] = useState<StepRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('all');
  const [view, setView] = useState<'live' | 'shot'>(run.live ? 'live' : 'shot');
  const lastStep = useRef(0);
  const listRef = useRef<HTMLDivElement>(null);
  const live = run.live;

  // 切换对局:整局读一遍
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSteps([]);
    setView(live ? 'live' : 'shot');
    lastStep.current = 0;
    api<StepRecord[]>(`/api/runs/${enc(run.id)}/steps?limit=2000`)
      .then((rows) => {
        if (cancelled) return;
        setSteps(rows);
        lastStep.current = rows.at(-1)?.step ?? 0;
        setCursor(Math.max(0, rows.length - 1));
        setFollow(true);
      })
      .catch(() => {})
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // 只在换局时整局读;进行中 → 结束不重新读
  }, [run.id]);

  // 进行中的一局:增量拉新步骤
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => {
      api<StepRecord[]>(`/api/runs/${enc(run.id)}/steps?after=${lastStep.current}`)
        .then((rows) => {
          if (!rows.length) return;
          lastStep.current = rows.at(-1)!.step;
          setSteps((prev) => [...prev, ...rows]);
        })
        .catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [run.id, live]);

  useEffect(() => {
    if (follow && steps.length) setCursor(steps.length - 1);
  }, [steps, follow]);

  const visible = useMemo(
    () => steps.map((s, i) => ({ s, i })).filter(({ s }) => filter === 'all' || s.decision),
    [steps, filter],
  );

  // 只滚步骤列表,不带动整页(scrollIntoView 会把窗口也滚过去)
  useEffect(() => {
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>('[aria-current="true"]');
    if (!list || !row) return;
    const box = list.getBoundingClientRect();
    const r = row.getBoundingClientRect();
    if (r.top < box.top) list.scrollTop -= box.top - r.top;
    else if (r.bottom > box.bottom) list.scrollTop += r.bottom - box.bottom;
  }, [cursor, filter]);

  const go = (i: number) => {
    const next = Math.max(0, Math.min(steps.length - 1, i));
    setCursor(next);
    setFollow(next === steps.length - 1);
    setView('shot');
  };
  const canLive = liveAvailable && live;
  const showLive = canLive && view === 'live';
  const step = steps[cursor];
  const notes = useMemo(() => {
    for (let i = cursor; i >= 0; i--) {
      const n = steps[i]?.decision?.run_notes;
      if (n) return n;
    }
    return '';
  }, [steps, cursor]);

  return (
    <div className={styles.viewer}>
      <section className={styles.stage} aria-label="截图">
        {showLive && !step ? (
          <LiveView />
        ) : loading && !steps.length ? (
          <Skeleton className={styles.shotSkeleton} />
        ) : step ? (
          <>
            {showLive ? <LiveView /> : <ShotView runId={run.id} step={step} />}
            <div className={styles.stepbar}>
              {canLive && (
                <SegmentedControl label="画面" value={view} onValueChange={(v) => setView(v as 'live' | 'shot')}
                  options={[{ value: 'live', label: '实时' }, { value: 'shot', label: '截图' }]} />
              )}
              <Button variant="ghost" size="sm" aria-label="上一步" onClick={() => go(cursor - 1)} disabled={cursor === 0}>
                <ChevronLeft size={16} strokeWidth={1.75} aria-hidden="true" />
              </Button>
              <input type="range" className={styles.range} min={0} max={Math.max(0, steps.length - 1)} value={cursor}
                aria-label="选择步骤" onChange={(e) => go(Number(e.target.value))} />
              <Button variant="ghost" size="sm" aria-label="下一步" onClick={() => go(cursor + 1)}
                disabled={cursor >= steps.length - 1}>
                <ChevronRight size={16} strokeWidth={1.75} aria-hidden="true" />
              </Button>
              <span className={styles.stepCount}>
                {step.step} / {steps.at(-1)?.step}
              </span>
              {live && !follow && (
                <Button variant="secondary" size="sm" onClick={() => go(steps.length - 1)}>
                  <ChevronsRight size={16} strokeWidth={1.75} aria-hidden="true" />
                  最新
                </Button>
              )}
            </div>

            <div className={styles.timeline}>
              <SegmentedControl label="步骤筛选" value={filter} onValueChange={setFilter}
                options={[{ value: 'all', label: '全部' }, { value: 'decision', label: '模型决策' }]} />
              <div className={styles.stepList} ref={listRef}>
                {visible.map(({ s, i }) => (
                  <button key={s.step} type="button" className={styles.stepRow} aria-current={i === cursor ? 'true' : undefined}
                    onClick={() => go(i)}>
                    <span className={styles.stepNo}>{s.step}</span>
                    <span className={styles.stepTs}>{s.ts}</span>
                    <span className={styles.stepNode}>{s.node ?? ''}</span>
                    <span className={styles.stepScreen} data-decision={s.decision ? 'true' : undefined}>{screenName(s.screen)}</span>
                    <span className={styles.stepText}>
                      {s.error ? `出错:${s.error}` : s.decision?.analysis || (s.actions ?? []).join(';') || s.note || ''}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          </>
        ) : (
          <EmptyState title="这局还没有步骤" description="agent 截到第一张图后会显示在这里。" />
        )}
      </section>

      <aside className={styles.panel} aria-label="这一步的判断">
        {step ? <StepDetail step={step} notes={notes} run={run} /> : <Skeleton className={styles.panelSkeleton} />}
      </aside>
    </div>
  );
}
