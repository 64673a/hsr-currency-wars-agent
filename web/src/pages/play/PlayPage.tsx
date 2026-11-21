import { useCallback, useEffect, useRef, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Button } from '@/components/arc/button/button';
import { CopyButton } from '@/components/arc/copy-button/copy-button';
import { Input } from '@/components/arc/input/input';
import { NumberField } from '@/components/arc/number-field/number-field';
import { Select } from '@/components/arc/select/select';
import { Skeleton } from '@/components/arc/skeleton/skeleton';
import {
  api, EFFORTS, effortName, enc, errorText, EXTERNAL_AGENT,
  type ExternalAgent, type ModelProfile, type PlayStatus, type RunSummary,
} from '@/lib/api';
import page from '@/lib/page.module.css';
import { useToast } from '@/lib/toast';

import styles from './play.module.css';
import { LiveView } from './live';
import { RunBadge, RunLine, runProgress, runSetup, shortTime } from './runs';
import { RunViewer } from './viewer';

const POLL_MS = 2000;

/** 开局时不改思考强度,用模型档案里的设置 */
const PROFILE_EFFORT = 'profile';

/** 开局表单上次改过的值,存在这个浏览器里(和主题一样),刷新或打完一局后照旧;只记用户改过的项,没改的跟着配置走 */
const START_KEY = 'cw-start';
type StartPrefs = { model?: string; effort?: string; difficulty?: string; rounds?: number };

function loadPrefs(): StartPrefs {
  try {
    const v = JSON.parse(localStorage.getItem(START_KEY) ?? '{}');
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}

function savePrefs(patch: StartPrefs) {
  try {
    localStorage.setItem(START_KEY, JSON.stringify({ ...loadPrefs(), ...patch }));
  } catch {
    // 隐私模式下存不了,只影响下次打开
  }
}

/** 外部 agent 用的模型和思考强度:用户给每个连上来过的 agent 填,离开输入框时保存;还没有 agent 连上来过时不显示 */
function ExternalAgentFields() {
  const [agents, setAgents] = useState<ExternalAgent[]>([]);
  const [name, setName] = useState('');
  const [draft, setDraft] = useState({ model: '', effort: '' });
  const [error, setError] = useState('');

  useEffect(() => {
    const load = () => api<ExternalAgent[]>('/api/external-agents').then(setAgents).catch(() => {});
    load();
    const t = window.setInterval(load, POLL_MS);
    return () => window.clearInterval(t);
  }, []);

  const current = agents.find((a) => a.name === name);

  const pick = useCallback((agent: ExternalAgent) => {
    setName(agent.name);
    setDraft({ model: agent.model, effort: agent.effort });
    setError('');
  }, []);

  // 默认选最近连上来的;列表刷新时不动正在填的内容
  useEffect(() => {
    if (!current && agents.length) pick(agents[0]);
  }, [agents, current, pick]);

  const save = async () => {
    const body = { model: draft.model.trim(), effort: draft.effort.trim() };
    if (!current || (body.model === current.model && body.effort === current.effort)) return;
    try {
      const saved = await api<ExternalAgent>(`/api/external-agents/${enc(name)}`, { method: 'PUT', body });
      setAgents((list) => list.map((a) => (a.name === saved.name ? saved : a)));
      setError('');
    } catch (e) {
      setError(errorText(e));
    }
  };

  if (!current) return null;
  return (
    <>
      <Select label="agent" options={agents.map((a) => ({ value: a.name, label: a.name }))} value={name}
        onValueChange={(v) => pick(agents.find((a) => a.name === v) ?? current)} />
      <div className={page.fields2}>
        <Input label="模型 ID" placeholder="claude-opus-5-5" value={draft.model} error={error || undefined}
          onChange={(e) => setDraft({ ...draft, model: e.target.value })} onBlur={save} />
        <Input label="思考强度" placeholder="high" value={draft.effort}
          onChange={(e) => setDraft({ ...draft, effort: e.target.value })} onBlur={save} />
      </div>
    </>
  );
}

/** 开局表单(右栏) */
function StartForm({ onChange }: { onChange: () => void }) {
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
        // 存下的值不再有效(档案删了、档位改了)就用配置里的默认
        const saved = loadPrefs();
        const names = [...ms.map((m) => m.name), EXTERNAL_AGENT];
        const efforts: string[] = [PROFILE_EFFORT, ...EFFORTS.map((e) => e.value)];
        setModel(saved.model && names.includes(saved.model) ? saved.model : (cfg.roles.tactical ?? cfg.roles.default ?? ms[0]?.name ?? EXTERNAL_AGENT));
        setEffort(saved.effort && efforts.includes(saved.effort) ? saved.effort : PROFILE_EFFORT);
        setDifficulty(typeof saved.difficulty === 'string' ? saved.difficulty : cfg.agent.difficulty);
        setRounds(Number.isInteger(saved.rounds) && saved.rounds! >= 1 && saved.rounds! <= 50 ? saved.rounds! : 1);
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  const pickModel = (v: string) => {
    setModel(v);
    savePrefs({ model: v });
  };
  const pickEffort = (v: string) => {
    setEffort(v);
    savePrefs({ effort: v });
  };
  const pickDifficulty = (v: string) => {
    setDifficulty(v);
    savePrefs({ difficulty: v });
  };
  const pickRounds = (v: number) => {
    setRounds(v);
    savePrefs({ rounds: v });
  };

  const profileEffort = effortName(models.find((m) => m.name === model)?.reasoning_effort);
  const mcp = `${window.location.origin}/mcp`;

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

  return (
    <div className={`${page.fields} ${styles.form}`}>
      <Select label="模型"
        options={[...models.map((m) => ({ value: m.name, label: m.name })), { value: EXTERNAL_AGENT, label: '外部 agent(MCP)' }]}
        value={model} onValueChange={pickModel} />
      {model === EXTERNAL_AGENT ? (
        <>
          <div className={styles.mcp}>
            <span className={styles.mcpLabel}>MCP 地址</span>
            <span className={styles.mcpValue}>{mcp}</span>
            <CopyButton value={mcp} label="复制" iconOnly variant="plain" />
          </div>
          <ExternalAgentFields />
        </>
      ) : (
        <Select label="思考强度"
          options={[{ value: PROFILE_EFFORT, label: profileEffort ? `档案设置(${profileEffort})` : '档案设置' }, ...EFFORTS]}
          value={effort} onValueChange={pickEffort} />
      )}
      <div className={page.fields2}>
        <Input label="难度" placeholder="A8-1" value={difficulty} onChange={(e) => pickDifficulty(e.target.value)} />
        <NumberField label="局数" min={1} max={50} value={rounds} onValueChange={pickRounds} />
      </div>
      {error && <Alert tone="danger" title="没有开始">{error}</Alert>}
      <Button className={styles.startButton} onClick={start} loading={busy} disabled={!model}>开始对局</Button>
    </div>
  );
}

/** 进行中:标题行右边的状态和停止按钮 */
function RunningControls({ status, onChange }: { status: PlayStatus; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
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
      {status.profile === EXTERNAL_AGENT && status.agent.waiting && <span className={styles.round}>等 agent 回答</span>}
      {status.rounds > 1 && <span className={styles.round}>第 {status.round} / {status.rounds} 局</span>}
      {status.hotkey && <span className={styles.round}>{status.hotkey.replace(/\b\w/g, (c) => c.toUpperCase())} 停止</span>}
      <Button variant="danger" onClick={stop} loading={busy || status.stopping}>
        {status.stopping ? '正在停止' : '停止对局'}
      </Button>
    </div>
  );
}

/** 上一局的结果(右栏),回放在对局记录页 */
function LastRun({ run, error }: { run: RunSummary; error: string | null }) {
  return (
    <section className={styles.last} aria-label="上一局">
      <h2 className={styles.blockTitle}>上一局</h2>
      <div className={styles.lastBody}>
        <span className={styles.runTop}>
          <span className={styles.runTime}>{shortTime(run.started_at)}</span>
          <RunBadge run={run} />
        </span>
        <span className={styles.runMeta}>{runSetup(run)}</span>
        <span className={styles.runMeta}>{runProgress(run)}</span>
      </div>
      {error && <Alert tone="danger" title="出错">{error}</Alert>}
      <Button variant="secondary" size="sm" className={styles.lastButton}
        onClick={() => (window.location.hash = `#/runs?run=${enc(run.id)}`)}>
        查看回放
      </Button>
    </section>
  );
}

export default function PlayPage() {
  const toast = useToast();
  const [status, setStatus] = useState<PlayStatus | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
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

  // 对局页只看进行中的一局;打完回到开局的样子,回放在对局记录页
  const current = runs?.find((r) => r.live) ?? null;
  const last = runs?.find((r) => !r.live) ?? null;
  const running = status?.running ?? false;
  const canStart = Boolean(status?.available) && !running;

  return (
    <div className={`${page.page} ${page.wide} ${page.fill}`}>
      <header className={styles.header}>
        <div className={styles.headerMain}>
          <h1 className={styles.title}>对局</h1>
          {current && <RunLine run={current} />}
        </div>
        {status && running && <RunningControls status={status} onChange={refresh} />}
      </header>

      {runs === null ? (
        <Skeleton className={styles.skeleton} />
      ) : current ? (
        <div className={styles.frame}>
          <RunViewer key={current.id} run={current} liveAvailable={liveAvailable} />
        </div>
      ) : (
        <div className={styles.frame}>
          <div className={styles.idle}>
            <section className={styles.idleStage} aria-label="游戏画面">
              {liveAvailable ? (
                <LiveView />
              ) : (
                <figure className={styles.shot}>
                  <span className={styles.noShot}>没有实时画面</span>
                </figure>
              )}
            </section>
            {(canStart || last) && (
              <aside className={styles.side}>
                {canStart && <StartForm onChange={refresh} />}
                {last && <LastRun run={last} error={status?.run_id === last.id ? status.error : null} />}
              </aside>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
