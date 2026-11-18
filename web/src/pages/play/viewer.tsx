import { ChevronLeft, ChevronRight, ChevronsRight } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Button } from '@/components/arc/button/button';
import EmptyState from '@/components/arc/empty-state/empty-state';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { api, enc, type PendingStep, type RunSummary, type StepRecord } from '@/lib/api';

import styles from './play.module.css';
import { LiveView } from './live';
import { PendingDetail, ShotView, StepDetail, screenName, type Deciding } from './step';

const POLL_MS = 2000;
const PENDING_MS = 500;
/** 决策结束后程序还要执行操作,这一步才写进记录:这段时间里仍显示这一步(最多这么久) */
const EXECUTE_MS = 60000;

/** 一局的逐步回看:左边截图(进行中的一局可切到实时画面)和步骤列表,右边这一步的判断。占满外层 .frame 的高度。
 *  进行中的一局,模型还没答完时这一步就出现在列表末尾,右边实时显示它的思考和判断。 */
export function RunViewer({ run, liveAvailable }: { run: RunSummary; liveAvailable: boolean }) {
  const [steps, setSteps] = useState<StepRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('all');
  const [view, setView] = useState<'live' | 'shot'>(run.live ? 'live' : 'shot');
  const [pending, setPending] = useState<Deciding | null>(null);
  const lastStep = useRef(0);
  const current = useRef<Deciding | null>(null);   // 最近一次读到的决策中的那一步(轮询回调里用)
  const listRef = useRef<HTMLDivElement>(null);
  const live = run.live;

  // 切换对局:整局读一遍
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setSteps([]);
    current.current = null;
    setPending(null);
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
  const pull = useCallback(() => {
    api<StepRecord[]>(`/api/runs/${enc(run.id)}/steps?after=${lastStep.current}`)
      .then((rows) => {
        if (!rows.length || rows[0].step <= lastStep.current) return;
        lastStep.current = rows.at(-1)!.step;
        setSteps((prev) => [...prev, ...rows]);
      })
      .catch(() => {});
  }, [run.id]);

  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(pull, POLL_MS);
    return () => window.clearInterval(t);
  }, [live, pull]);

  // 模型决策中的那一步:答完以后程序还在执行操作,先留着,等这一步的记录出来再换掉
  useEffect(() => {
    if (!live) {
      current.current = null;
      setPending(null);
      return;
    }
    const tick = () =>
      api<PendingStep | Record<string, never>>(`/api/runs/${enc(run.id)}/pending`)
        .then((p) => {
          const now = Date.now();
          const prev = current.current;
          let next: Deciding | null = null;
          if ('step' in p) next = { data: p as PendingStep, at: now, done: false };
          else if (prev && prev.data.step > lastStep.current && now - prev.at < EXECUTE_MS) next = { ...prev, done: true };
          current.current = next;
          setPending(next);
          if (next?.done) pull();     // 这一步正在执行操作,记录随时会出来
        })
        .catch(() => {});
    tick();
    const t = window.setInterval(tick, PENDING_MS);
    return () => window.clearInterval(t);
  }, [run.id, live, pull]);

  const deciding = pending && pending.data.step > (steps.at(-1)?.step ?? 0) ? pending : null;
  const total = steps.length + (deciding ? 1 : 0);

  useEffect(() => {
    if (follow && total) setCursor(total - 1);
  }, [total, follow]);

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
  }, [cursor, filter, total]);

  const go = (i: number) => {
    const next = Math.max(0, Math.min(total - 1, i));
    setCursor(next);
    setFollow(next === total - 1);
    setView('shot');
  };
  const canLive = liveAvailable && live;
  const showLive = canLive && view === 'live';
  const atPending = deciding !== null && cursor >= steps.length;
  const step = atPending ? undefined : steps[Math.min(cursor, steps.length - 1)];
  const notes = useMemo(() => {
    for (let i = Math.min(cursor, steps.length - 1); i >= 0; i--) {
      const n = steps[i]?.decision?.run_notes;
      if (n) return n;
    }
    return '';
  }, [steps, cursor]);
  const pendingShot = deciding?.data.vision?.[0] ?? deciding?.data.shot;

  return (
    <div className={styles.viewer}>
      <section className={styles.stage} aria-label="截图">
        {showLive && !step && !deciding ? (
          <LiveView />
        ) : loading && !steps.length ? (
          <Skeleton className={styles.shotSkeleton} />
        ) : step || deciding ? (
          <>
            {showLive ? (
              <LiveView />
            ) : step ? (
              <ShotView runId={run.id} step={step} />
            ) : (
              <figure className={styles.shot}>
                {pendingShot ? (
                  <img src={`/api/runs/${enc(run.id)}/${pendingShot}`} alt={`第 ${deciding!.data.step} 步模型正在看的截图`} />
                ) : (
                  <span className={styles.noShot}>这一步没有截图</span>
                )}
              </figure>
            )}
            <div className={styles.stepbar}>
              {canLive && (
                <SegmentedControl label="画面" value={view} onValueChange={(v) => setView(v as 'live' | 'shot')}
                  options={[{ value: 'live', label: '实时' }, { value: 'shot', label: '截图' }]} />
              )}
              <Button variant="ghost" size="sm" aria-label="上一步" onClick={() => go(cursor - 1)} disabled={cursor === 0}>
                <ChevronLeft size={16} strokeWidth={1.75} aria-hidden="true" />
              </Button>
              <input type="range" className={styles.range} min={0} max={Math.max(0, total - 1)} value={cursor}
                aria-label="选择步骤" onChange={(e) => go(Number(e.target.value))} />
              <Button variant="ghost" size="sm" aria-label="下一步" onClick={() => go(cursor + 1)}
                disabled={cursor >= total - 1}>
                <ChevronRight size={16} strokeWidth={1.75} aria-hidden="true" />
              </Button>
              <span className={styles.stepCount}>
                {(step?.step ?? deciding?.data.step)} / {deciding ? deciding.data.step : steps.at(-1)?.step}
              </span>
              {live && !follow && (
                <Button variant="secondary" size="sm" onClick={() => go(total - 1)}>
                  <ChevronsRight size={16} strokeWidth={1.75} aria-hidden="true" />
                  最新
                </Button>
              )}
              <SegmentedControl label="步骤筛选" value={filter} onValueChange={setFilter}
                options={[{ value: 'all', label: '全部' }, { value: 'decision', label: '模型决策' }]} />
            </div>

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
              {deciding && (
                <button type="button" className={styles.stepRow} data-pending="true"
                  aria-current={atPending ? 'true' : undefined} onClick={() => go(total - 1)}>
                  <span className={styles.stepNo}>{deciding.data.step}</span>
                  <span className={styles.stepTs}>{deciding.data.ts}</span>
                  <span className={styles.stepNode}>{deciding.data.node ?? ''}</span>
                  <span className={styles.stepScreen} data-decision="true">{screenName(deciding.data.screen)}</span>
                  <span className={styles.stepText}>
                    <span className={styles.pendingDot} aria-hidden="true" />
                    {deciding.done ? '正在执行操作' : deciding.data.analysis || '模型决策中'}
                  </span>
                </button>
              )}
            </div>
          </>
        ) : (
          <EmptyState title="这局还没有步骤" description="agent 截到第一张图后会显示在这里。" />
        )}
      </section>

      <aside className={styles.panel} aria-label="这一步的判断">
        {atPending && deciding ? (
          <PendingDetail pending={deciding} />
        ) : step ? (
          <StepDetail step={step} notes={notes} run={run} />
        ) : (
          <Skeleton className={styles.panelSkeleton} />
        )}
      </aside>
    </div>
  );
}
