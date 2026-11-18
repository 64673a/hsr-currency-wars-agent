import { useEffect, useRef, useState } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge } from '@/components/arc/badge/badge';
import { type BattleRecord, enc, type PendingStep, type RunSummary, type StepRecord, type StepState } from '@/lib/api';

import { BattleChart } from './BattleChart';
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
  battle_end: '战斗结束',
  node_result: '节点结算',
  deploy_confirm: '出战确认',
  strategy_choice: '投资策略',
  encounter_choice: '遭遇节点',
  tea_break: '幕后茶歇',
  rail_shop: '开拓商店',
  black_market: '黑市商店',
  ranger: '巡海游侠',
  dance: '梦境交织',
  garden: '奇兽花园',
  hack: '骇入效果',
  spotlight_replace: '替换光环',
  spotlight_info: '光环详情',
  reconnect: '断线重连',
  disconnected: '连接中断',
  empty_front: '前台无角色',
  other: '其他',
};

export function screenName(screen?: string) {
  return screen ? (SCREENS[screen] ?? screen) : '';
}

const ROLES: Record<string, string> = { tactical: '备战', strategic: '战略', chooser: '三选一', navigator: '导航' };

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
      {step.vision?.[0] || step.shot ? (
        <img src={`/api/runs/${enc(runId)}/${step.vision?.[0] || step.shot}`} alt={`第 ${step.step} 步${step.vision?.length ? '模型输入' : ''}截图`} />
      ) : (
        <span className={styles.noShot}>这一步没有截图</span>
      )}
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

function BattleView({ battle }: { battle: BattleRecord }) {
  const deaths = battle.deaths ?? [];
  const facts = [
    battle.seconds !== undefined && `用时 ${Math.round(battle.seconds)} 秒`,
    battle.progress !== null && `进度 ${battle.progress}%`,
    battle.av !== null && `剩余行动值 ${battle.av}`,
    battle.samples && `阵亡 ${deaths.length} 次`,
  ].filter(Boolean);
  return (
    <section className={styles.block}>
      <h2 className={styles.blockTitle}>战斗</h2>
      {facts.length > 0 && <p className={styles.facts}>{facts.join(' · ')}</p>}
      <BattleChart battle={battle} />
      {deaths.length > 0 && (
        <ul className={styles.events}>
          {deaths.map((d, i) => (
            <li key={i}>
              第 {Math.round(d.t)} 秒有角色阵亡{d.av !== null && d.after !== null && `,行动值 ${d.av} → ${d.after}`}
            </li>
          ))}
        </ul>
      )}
      {(battle.events ?? []).length > 0 && (
        <ul className={styles.events}>
          {battle.events!.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

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
        {step.screen && (
          <Badge size="sm" color={d ? 'violet' : undefined}>
            {screenName(step.screen)}
          </Badge>
        )}
        {step.node && <span className={styles.detailNode}>{step.node}</span>}
        <span className={styles.detailMeta}>{[`第 ${step.step} 步`, step.ts].filter(Boolean).join(' · ')}</span>
      </div>

      {step.error && <Alert tone="danger" title="这一步出错">{step.error}</Alert>}
      {d?.error && <Alert tone="warning" title="模型没有给出有效操作">{d.error}</Alert>}
      {step.perception_issues?.length ? <Alert tone="warning" title="感知识别待核对">{step.perception_issues.join('；')}</Alert> : null}
      {step.vision?.slice(1).map((path) => <figure className={styles.shot} key={path}>
        <img src={`/api/runs/${enc(run?.id ?? '')}/${path}`} alt="模型查看的局部放大图" />
      </figure>)}

      {step.battle && <BattleView battle={step.battle} />}
      {step.battle_summary && !step.battle?.samples && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>上一场战斗</h2>
          <p className={styles.analysis}>{step.battle_summary}</p>
        </section>
      )}
      {step.plane_nodes && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>本位面节点</h2>
          <p className={styles.foldText}>{step.plane_nodes}</p>
        </section>
      )}
      {step.affixes?.length ? (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>敌人词条</h2>
          <p className={styles.foldText}>{step.affixes.map((a) => `${a.name}：${a.desc || '没读到说明'}`).join('\n')}</p>
        </section>
      ) : null}
      {step.companions?.length ? (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>本局奇遇伙伴</h2>
          <p className={styles.foldText}>{step.companions.map((c) => `${c.name}：${c.text}`).join('\n')}</p>
        </section>
      ) : null}
      {step.state && <StateView state={step.state} />}

      {d?.analysis && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>局势</h2>
          <p className={styles.analysis}>{d.analysis}</p>
        </section>
      )}
      {d?.alternatives?.length ? (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>没采用的做法</h2>
          <ul className={styles.alternatives}>
            {d.alternatives.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </section>
      ) : null}

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
          {d.episode ? `回合 ${d.episode} 第 ${d.turn} 轮 · ${ROLES[d.role ?? ''] ?? d.role} · ` : ''}
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
      {d?.prompt && (
        <details className={styles.fold}>
          <summary>{d.turn === 1 ? '发给模型的内容' : '交给模型的执行结果和新界面'}</summary>
          <p className={styles.foldMono}>{d.prompt}</p>
        </details>
      )}
      {notes && (
        <details className={styles.fold} open>
          <summary>对局方向与计划</summary>
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

/** 决策中的那一步:at 是浏览器收到它的时间;done 表示模型答完了、程序正在执行操作 */
export interface Deciding {
  data: PendingStep;
  at: number;
  done: boolean;
}

/** 已经想了多久:按服务端的时间算(两台机器的钟可能差几秒),收到以后在浏览器里接着走 */
function useElapsed({ data, at, done }: Deciding) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (done) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [done]);
  return Math.max(0, Math.floor(data.now - data.started + (Math.max(now, at) - at) / 1000));
}

/** 流式的思考:新内容来了自动滚到底;往上翻看时不打扰 */
function Thinking({ text, cut }: { text: string; cut: boolean }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const stick = useRef(true);
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [text]);
  return (
    <p ref={ref} className={styles.thinking}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}>
      {cut ? '…' : ''}
      {text}
    </p>
  );
}

/** 右栏:模型还在决策的这一步,思考和判断边收边显示 */
export function PendingDetail({ pending }: { pending: Deciding }) {
  const p = pending.data;
  const elapsed = useElapsed(pending);
  const writing = !pending.done && !p.acting;
  const status = pending.done
    ? '正在执行操作'
    : p.external
      ? '等外部 agent 回答'
      : p.acting
        ? '正在给出操作'
        : p.alternatives !== null
          ? '正在写没采用的做法'
          : p.analysis !== null
            ? '正在写局势'
            : p.reasoning
              ? '正在思考'
              : '等模型回复';
  return (
    <div className={styles.detail}>
      <div className={styles.detailHead}>
        {p.screen && (
          <Badge size="sm" color="violet">
            {screenName(p.screen)}
          </Badge>
        )}
        {p.node && <span className={styles.detailNode}>{p.node}</span>}
        <span className={styles.detailMeta}>{[`第 ${p.step} 步`, p.ts].filter(Boolean).join(' · ')}</span>
      </div>

      <p className={styles.pendingStatus} aria-live="polite">
        <span className={styles.pendingDot} aria-hidden="true" />
        {[status, `${elapsed} 秒`, p.retries ? `重问第 ${p.retries} 次` : ''].filter(Boolean).join(' · ')}
      </p>

      {p.reasoning && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>思考</h2>
          <Thinking text={p.reasoning} cut={p.reasoning_cut} />
        </section>
      )}
      {p.analysis !== null && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>局势</h2>
          <p className={styles.analysis}>
            {p.analysis}
            {writing && p.alternatives === null && <span className={styles.caret} aria-hidden="true" />}
          </p>
        </section>
      )}
      {p.alternatives !== null && (p.alternatives.length > 0 || writing) && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>没采用的做法</h2>
          <ul className={styles.alternatives}>
            {p.alternatives.map((a, i) => (
              <li key={i}>
                {a}
                {writing && i === p.alternatives!.length - 1 && <span className={styles.caret} aria-hidden="true" />}
              </li>
            ))}
            {writing && p.alternatives.length === 0 && <li><span className={styles.caret} aria-hidden="true" /></li>}
          </ul>
        </section>
      )}
      {p.text && (
        <section className={styles.block}>
          <h2 className={styles.blockTitle}>回复</h2>
          <p className={styles.analysis}>{p.text}</p>
        </section>
      )}

      <p className={styles.usage}>
        {[p.episode ? `回合 ${p.episode} 第 ${p.turn} 轮` : '', ROLES[p.role ?? ''] ?? p.role, p.model].filter(Boolean).join(' · ')}
      </p>
    </div>
  );
}
