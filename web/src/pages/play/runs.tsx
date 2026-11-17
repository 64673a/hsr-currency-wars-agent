import { Badge } from '@/components/arc/badge/badge';
import { effortName, type RunSummary } from '@/lib/api';

import styles from './play.module.css';

export function shortTime(s: string) {
  // 2026-11-14 09:20:16 -> 11-14 09:20
  return s.length >= 16 ? `${s.slice(5, 10)} ${s.slice(11, 16)}` : s;
}

export function RunBadge({ run }: { run: RunSummary }) {
  if (run.live) return <Badge tone="info" size="sm" dot>进行中</Badge>;
  if (run.outcome === '通关') return <Badge tone="success" size="sm">通关</Badge>;
  if (run.outcome === '失败') return <Badge tone="danger" size="sm">失败</Badge>;
  if (run.result === 'stopped') return <Badge size="sm">已停止</Badge>;
  if (run.result === 'interrupted') return <Badge tone="warning" size="sm">人工接管</Badge>;
  if (run.result === 'error') return <Badge tone="warning" size="sm">出错</Badge>;
  return <Badge size="sm">未完成</Badge>;
}

/** 模型 · 思考强度 · 难度 */
export function runSetup(run: RunSummary) {
  return [run.profile, run.effort && `思考强度:${effortName(run.effort)}`, run.difficulty].filter(Boolean).join(' · ');
}

export function runProgress(run: RunSummary) {
  return `${run.node ? `到达 ${run.node}` : '未开局'} · ${run.steps} 步`;
}

/** 一局的概要,一行:时间、结果、模型和进度 */
export function RunLine({ run }: { run: RunSummary }) {
  return (
    <div className={styles.runLine}>
      <span className={styles.runTime}>{shortTime(run.started_at)}</span>
      <RunBadge run={run} />
      <span className={styles.runLineMeta}>
        {runSetup(run)} · {runProgress(run)}
      </span>
    </div>
  );
}
