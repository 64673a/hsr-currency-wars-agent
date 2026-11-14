import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { enc, type RunSummary, type StepRecord, type StepState } from '@/lib/api';

import styles from './play.module.css';

const SCREENS: Record<string, string> = {
  home: '主页',
  mode_select: '模式选择',
  difficulty: '难度选择',
  run_intro: '开局',
  environment_choice: '投资环境',
  prepare: '备战',
  battle: '战斗',
  spotlight_choice: '主角光环',
  supply: '补给',
  tap_continue: '点击继续',
  quit_dialog: '中断确认',
  settlement: '结算',
  other: '其他',
};

export function screenName(screen?: string) {
  return screen ? (SCREENS[screen] ?? screen) : '';
}

function tokens(n?: number) {
  if (!n) return '0';
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

// 截图上标出这一步实际点击 / 拖拽的位置(坐标是归一化的)
const W = 1600;
const H = 900;

export function ShotView({ runId, step }: { runId: string; step: StepRecord }) {
  const marks = step.marks ?? [];
  return (
    <figure className={styles.shot}>
      {step.shot && <img src={`/api/runs/${enc(runId)}/shots/${step.shot.replace(/^shots\//, '')}`} alt={`第 ${step.step} 步截图`} />}
      {marks.length > 0 && (
        <svg className={styles.overlay} viewBox={`0 0 ${W} ${H}`} aria-hidden="true">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0 0L10 5L0 10z" className={styles.markFill} />
            </marker>
          </defs>
          {marks.map((m, i) => {
            const x = m.x * W;
            const y = m.y * H;
            return (
              <g key={i}>
                {m.type === 'drag' && m.x2 !== undefined && m.y2 !== undefined && (
                  <line x1={x} y1={y} x2={m.x2 * W} y2={m.y2 * H} className={styles.markLine} markerEnd="url(#arrow)" />
                )}
                <circle cx={x} cy={y} r={20} className={m.type === 'scroll' ? styles.markScroll : styles.markRing} />
                {marks.length > 1 && (
                  <text x={x + 26} y={y - 18} className={styles.markLabel}>
                    {i + 1}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </figure>
  );
}

const ZONES = [
  { zone: 'front', label: '前台' },
  { zone: 'back', label: '后台' },
  { zone: 'bench', label: '备战席' },
] as const;

function StateView({ state }: { state: StepState }) {
  const h = state.hud ?? {};
  const facts = [
    h.hp !== undefined && `生命 ${h.hp}`,
    `金币 ${h.gold ?? '?'}`,
    h.level !== undefined && `等级 ${h.level}${h.exp ? `(${h.exp})` : ''}`,
    h.deployed && `上场 ${h.deployed}`,
  ].filter(Boolean);
  return (
    <section className={styles.block}>
      <h2 className={styles.blockTitle}>局面</h2>
      <p className={styles.facts}>{facts.join(' · ')}</p>
      {state.board && (
        <dl className={styles.board}>
          {ZONES.map(({ zone, label }) => {
            const units = state.board!.filter((u) => u.zone === zone);
            return (
              <div key={zone} className={styles.boardRow}>
                <dt>{label}</dt>
                <dd>
                  {units.length === 0 && <span className={styles.unitEmpty}>空</span>}
                  {units.map((u) => (
                    <span key={u.index} className={styles.unit} data-unknown={u.name ? undefined : 'true'}>
                      {u.name ?? '认不出'}
                      {u.stars > 0 && <span className={styles.stars}>{'★'.repeat(u.stars)}</span>}
                    </span>
                  ))}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </section>
  );
}

export function StepDetail({ step, notes, run }: { step: StepRecord; notes: string; run: RunSummary | null }) {
  const d = step.decision;
  const results = step.actions ?? [];
  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        <Badge size="sm" color={d ? 'violet' : undefined}>{screenName(step.screen)}</Badge>
        {step.node && <span className={styles.detailNode}>{step.node}</span>}
        <span className={styles.detailMeta}>
          第 {step.step} 步 · {step.ts}
        </span>
      </div>

      {step.error && <Alert tone="danger" title="这一步出错">{step.error}</Alert>}
      {d?.error && <Alert tone="warning" title="模型没有给出有效操作">{d.error}</Alert>}

      {step.state && <StateView state={step.state} />}

      {d?.analysis && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>判断</h2>
          <p className={styles.analysis}>{d.analysis}</p>
        </section>
      )}

      {results.length > 0 && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>
            操作
            {!d && <span className={styles.blockTag}>规则</span>}
          </h2>
          <ol className={styles.actions}>
            {results.map((r, i) => (
              <li key={i}>
                <span className={styles.actionText}>{r}</span>
                {d?.actions[i]?.reason && <span className={styles.actionReason}>{d.actions[i].reason}</span>}
              </li>
            ))}
          </ol>
        </section>
      )}

      {d && (
        <p className={styles.usage}>
          {d.model || run?.model} · {d.latency_s?.toFixed(1)} s · 输入 {tokens(d.usage?.input_tokens)}
          {d.usage?.cache_read_tokens ? `(缓存 ${tokens(d.usage.cache_read_tokens)})` : ''} · 输出 {tokens(d.usage?.output_tokens)}
        </p>
      )}

      {d?.reasoning && (
        <details className={styles.fold}>
          <summary>思考过程</summary>
          <p className={styles.foldText}>{d.reasoning}</p>
        </details>
      )}
      {notes && (
        <details className={styles.fold} open>
          <summary>本局笔记</summary>
          <p className={styles.foldText}>{notes}</p>
        </details>
      )}
      {step.texts && step.texts.length > 0 && (
        <details className={styles.fold}>
          <summary>识别到的文字</summary>
          <p className={styles.foldMono}>{step.texts.join('\n')}</p>
        </details>
      )}
    </div>
  );
}
