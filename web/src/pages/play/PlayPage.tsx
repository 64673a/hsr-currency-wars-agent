import { ChevronLeft, ChevronRight, ChevronsRight } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { Button } from '@/components/arc/button/button';
import EmptyState from '@/components/arc/empty-state/empty-state';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { api, enc, errorText, type ModelProfile, type PlayStatus, type RunSummary, type StepRecord } from '@/lib/api';
import page from '@/lib/page.module.css';
import { useToast } from '@/lib/toast';

import styles from './play.module.css';
import { LiveView } from './live';
import { ShotView, StepDetail, screenName } from './step';

const POLL_MS = 2000;

function shortTime(s: string) {
  // 2026-11-14 09:20:16 -> 11-14 09:20
  return s.length >= 16 ? `${s.slice(5, 10)} ${s.slice(11, 16)}` : s;
}

function RunBadge({ run }: { run: RunSummary }) {
  if (run.live) return <Badge tone="info" size="sm" dot>进行中</Badge>;
  if (run.outcome === '通关') return <Badge tone="success" size="sm">通关</Badge>;
  if (run.outcome === '失败') return <Badge tone="danger" size="sm">失败</Badge>;
  if (run.result === 'stopped') return <Badge size="sm">已停止</Badge>;
  if (run.result === 'interrupted') return <Badge tone="warning" size="sm">人工接管</Badge>;
  if (run.result === 'error') return <Badge tone="warning" size="sm">出错</Badge>;
  return <Badge size="sm">未完成</Badge>;
}

function StartControls({ status, onChange }: { status: PlayStatus; onChange: () => void }) {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [model, setModel] = useState('');
  const [difficulty, setDifficulty] = useState('');
  const [rounds, setRounds] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([api<ModelProfile[]>('/api/models'), api<{ roles: Record<string, string | null>; agent: { difficulty: string } }>('/api/config')])
      .then(([ms, cfg]) => {
        setModels(ms);
        setModel(cfg.roles.tactical ?? cfg.roles.default ?? ms[0]?.name ?? '');
        setDifficulty(cfg.agent.difficulty);
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      await api('/api/play/start', { method: 'POST', body: { model: model || null, difficulty: difficulty.trim() || null, rounds } });
      onChange();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const stop = async () => {
    setBusy(true);
    try {
      await api('/api/play/stop', { method: 'POST' });
      onChange();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.controls}>
      {status.running ? (
        <>
          {status.rounds > 1 && <span className={styles.round}>第 {status.round} / {status.rounds} 局</span>}
          {status.hotkey && <span className={styles.round}>{status.hotkey.replace(/\b\w/g, (c) => c.toUpperCase())} 停止</span>}
          <Button variant="danger" onClick={stop} loading={busy || status.stopping}>
            {status.stopping ? '正在停止' : '停止对局'}
          </Button>
        </>
      ) : (
        <>
          {models.length > 0 && (
            <Select label="模型" className={styles.modelSelect} options={models.map((m) => ({ value: m.name, label: m.name }))}
              value={model} onValueChange={setModel} />
          )}
          <Input label="难度" className={styles.difficulty} placeholder="A8-1" value={difficulty}
            onChange={(e) => setDifficulty(e.target.value)} />
          <div className={styles.rounds}>
            <NumberField label="局数" min={1} max={50} value={rounds} onValueChange={setRounds} />
          </div>
          <Button onClick={start} loading={busy} disabled={!model}>开始对局</Button>
        </>
      )}
      {error && <Alert tone="danger" title="没有开始" className={styles.controlsError}>{error}</Alert>}
    </div>
  );
}

export default function PlayPage() {
  const toast = useToast();
  const [status, setStatus] = useState<PlayStatus | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [steps, setSteps] = useState<StepRecord[]>([]);
  const [loadingSteps, setLoadingSteps] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [follow, setFollow] = useState(true);
  const [filter, setFilter] = useState('all');
  const [liveAvailable, setLiveAvailable] = useState(false);
  const [view, setView] = useState<'live' | 'shot'>('live');
  const lastStep = useRef(0);
  const wasRunning = useRef(false);
  const listRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(() => {
    api<PlayStatus>('/api/play').then(setStatus).catch(() => {});
    api<RunSummary[]>('/api/runs').then(setRuns).catch(() => setRuns([]));
    api<{ available: boolean }>('/api/live').then((r) => setLiveAvailable(r.available)).catch(() => setLiveAvailable(false));
  }, []);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  // 对局结束时提示一次
  useEffect(() => {
    if (!status) return;
    if (wasRunning.current && !status.running) {
      const why: Record<string, string> = { settlement: '打完了', stopped: '已停止', interrupted: '急停', timeout: '超时', max_steps: '步数用完' };
      toast('对局已结束', status.error ?? (status.result ? why[status.result] ?? status.result : undefined));
    }
    wasRunning.current = status.running;
  }, [status, toast]);

  // 默认选中进行中的一局,否则最新一局;新开一局时自动切过去
  const liveId = runs?.find((r) => r.live)?.id ?? null;
  useEffect(() => {
    if (!runs?.length) return;
    if (selected === null || (liveId && status?.run_id === liveId && selected !== liveId && follow)) {
      setSelected(liveId ?? runs[0].id);
    }
  }, [runs, liveId, selected, status?.run_id, follow]);

  const run = runs?.find((r) => r.id === selected) ?? null;

  // 切换对局:整局读一遍
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setLoadingSteps(true);
    setSteps([]);
    lastStep.current = 0;
    api<StepRecord[]>(`/api/runs/${enc(selected)}/steps?limit=2000`)
      .then((rows) => {
        if (cancelled) return;
        setSteps(rows);
        lastStep.current = rows.at(-1)?.step ?? 0;
        setCursor(Math.max(0, rows.length - 1));
        setFollow(true);
      })
      .catch(() => {})
      .finally(() => !cancelled && setLoadingSteps(false));
    return () => {
      cancelled = true;
    };
  }, [selected]);

  // 进行中的一局:增量拉新步骤
  const live = run?.live ?? false;
  useEffect(() => {
    if (!selected || !live) return;
    const t = window.setInterval(() => {
      api<StepRecord[]>(`/api/runs/${enc(selected)}/steps?after=${lastStep.current}`)
        .then((rows) => {
          if (!rows.length) return;
          lastStep.current = rows.at(-1)!.step;
          setSteps((prev) => [...prev, ...rows]);
        })
        .catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(t);
  }, [selected, live]);

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
  const showLive = liveAvailable && view === 'live';
  const step = steps[cursor];
  const notes = useMemo(() => {
    for (let i = cursor; i >= 0; i--) {
      const n = steps[i]?.decision?.run_notes;
      if (n) return n;
    }
    return '';
  }, [steps, cursor]);

  return (
    <div className={`${page.page} ${page.wide}`}>
      <header className={styles.header}>
        <h1 className={styles.title}>对局</h1>
        {status?.available && (status.running || !liveId) && <StartControls status={status} onChange={refresh} />}
      </header>

      {runs === null ? (
        <Skeleton className={styles.skeleton} />
      ) : runs.length === 0 ? (
        liveAvailable ? <LiveView /> : <EmptyState title="还没有对局" description="开始一局后,这里会逐步显示截图和模型的判断。" />
      ) : (
        <div className={styles.body}>
          <nav className={styles.runs} aria-label="对局列表">
            {runs.map((r) => (
              <button key={r.id} type="button" className={styles.run} aria-current={r.id === selected ? 'true' : undefined}
                onClick={() => {
                  setSelected(r.id);
                  setView(r.live ? 'live' : 'shot');
                }}>
                <span className={styles.runTop}>
                  <span className={styles.runTime}>{shortTime(r.started_at)}</span>
                  <RunBadge run={r} />
                </span>
                <span className={styles.runMeta}>
                  {r.profile}
                  {r.difficulty && ` · ${r.difficulty}`}
                </span>
                <span className={styles.runMeta}>
                  {r.node ? `到达 ${r.node}` : '未开局'} · {r.steps} 步
                </span>
              </button>
            ))}
          </nav>

          <section className={styles.stage} aria-label="截图">
            {showLive && !step ? (
              <LiveView />
            ) : loadingSteps && !steps.length ? (
              <Skeleton className={styles.shotSkeleton} />
            ) : step ? (
              <>
                {showLive ? <LiveView /> : <ShotView runId={selected!} step={step} />}
                <div className={styles.stepbar}>
                  {liveAvailable && (
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
      )}
    </div>
  );
}
