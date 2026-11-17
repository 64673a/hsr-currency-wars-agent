import { useCallback, useEffect, useRef, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Button } from '@/components/arc/button/button';
import EmptyState from '@/components/arc/empty-state/empty-state';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import { api, EFFORTS, effortName, enc, errorText, EXTERNAL_AGENT, type ModelProfile, type PlayStatus, type RunSummary } from '@/lib/api';
import page from '@/lib/page.module.css';
import { useToast } from '@/lib/toast';

import styles from './play.module.css';
import { LiveView } from './live';
import { RunLine } from './runs';
import { RunViewer } from './viewer';

const POLL_MS = 2000;

/** 开局时不改思考强度,用模型档案里的设置 */
const PROFILE_EFFORT = 'profile';

function StartControls({ status, onChange }: { status: PlayStatus; onChange: () => void }) {
  const [models, setModels] = useState<ModelProfile[]>([]);
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState(PROFILE_EFFORT);
  const [difficulty, setDifficulty] = useState('');
  const [rounds, setRounds] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([api<ModelProfile[]>('/api/models'), api<{ roles: Record<string, string | null>; agent: { difficulty: string } }>('/api/config')])
      .then(([ms, cfg]) => {
        setModels(ms);
        setModel(cfg.roles.tactical ?? cfg.roles.default ?? ms[0]?.name ?? EXTERNAL_AGENT);
        setDifficulty(cfg.agent.difficulty);
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  const profileEffort = effortName(models.find((m) => m.name === model)?.reasoning_effort);

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      const override = model !== EXTERNAL_AGENT && effort !== PROFILE_EFFORT ? effort : null;
      await api('/api/play/start', { method: 'POST', body: { model: model || null, effort: override, difficulty: difficulty.trim() || null, rounds } });
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
          {status.profile === EXTERNAL_AGENT && status.agent.waiting && <span className={styles.round}>等 agent 回答</span>}
          {status.rounds > 1 && <span className={styles.round}>第 {status.round} / {status.rounds} 局</span>}
          {status.hotkey && <span className={styles.round}>{status.hotkey.replace(/\b\w/g, (c) => c.toUpperCase())} 停止</span>}
          <Button variant="danger" onClick={stop} loading={busy || status.stopping}>
            {status.stopping ? '正在停止' : '停止对局'}
          </Button>
        </>
      ) : (
        <>
          {model === EXTERNAL_AGENT && <span className={styles.round}>MCP 地址 {window.location.origin}/mcp</span>}
          <Select label="模型" className={styles.modelSelect}
            options={[...models.map((m) => ({ value: m.name, label: m.name })), { value: EXTERNAL_AGENT, label: '外部 agent(MCP)' }]}
            value={model} onValueChange={setModel} />
          {model !== EXTERNAL_AGENT && (
            <Select label="思考强度" className={styles.effortSelect}
              options={[{ value: PROFILE_EFFORT, label: profileEffort ? `档案设置(${profileEffort})` : '档案设置' }, ...EFFORTS]}
              value={effort} onValueChange={setEffort} />
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
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [liveAvailable, setLiveAvailable] = useState(false);
  const wasRunning = useRef(false);

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

  // 只看当前这一局:进行中的一局出现就切过去;打完后留在页面上,直到开下一局(以前的对局在「对局记录」里)
  const liveId = runs?.find((r) => r.live)?.id ?? null;
  useEffect(() => {
    if (liveId) setCurrentId(liveId);
  }, [liveId]);
  const current = runs?.find((r) => r.id === currentId) ?? null;

  return (
    <div className={`${page.page} ${page.wide}`}>
      <header className={styles.header}>
        <h1 className={styles.title}>对局</h1>
        {status?.available && (status.running || !liveId) && <StartControls status={status} onChange={refresh} />}
      </header>

      {runs === null ? (
        <Skeleton className={styles.skeleton} />
      ) : current ? (
        <div className={styles.current}>
          <div className={styles.currentHead}>
            <RunLine run={current} />
            {!current.live && (
              <Button variant="ghost" size="sm" onClick={() => (window.location.hash = `#/runs?run=${enc(current.id)}`)}>
                在对局记录中查看
              </Button>
            )}
          </div>
          <RunViewer key={current.id} run={current} liveAvailable={liveAvailable} />
        </div>
      ) : liveAvailable ? (
        <LiveView />
      ) : (
        <EmptyState title="没有进行中的对局" description="开始一局后,这里会显示实时画面和模型每一步的判断。" />
      )}
    </div>
  );
}
