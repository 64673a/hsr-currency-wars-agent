import { useState } from 'react';

import type { BattleRecord } from '@/lib/api';

import styles from './play.module.css';

// 一场战斗的读数:上面是剩余行动值,下面是进度(两种量纲不共用纵轴),共用时间轴;阵亡标在行动值曲线上。
const W = 560;
const PAD_L = 36;
const PAD_R = 12;
const AV_H = 96;
const PR_H = 64;
const PAD_Y = 10;

type Point = { t: number; v: number };

function path(points: Point[], x: (t: number) => number, y: (v: number) => number) {
  return points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('');
}

function nearest(points: Point[], t: number) {
  let best: Point | undefined;
  for (const p of points) if (p.t <= t + 0.5 && (!best || p.t > best.t)) best = p;
  return best;
}

export function BattleChart({ battle }: { battle: BattleRecord }) {
  const [hover, setHover] = useState<number | null>(null);
  const samples = battle.samples ?? [];
  const av = samples.filter((s) => s[1] !== null).map((s) => ({ t: s[0], v: s[1] as number }));
  const pr = samples.filter((s) => s[2] !== null).map((s) => ({ t: s[0], v: s[2] as number }));
  const deaths = battle.deaths ?? [];
  const end = Math.max(battle.seconds ?? 0, ...samples.map((s) => s[0]), 1);
  if (av.length + pr.length < 2) return null;

  const x = (t: number) => PAD_L + (t / end) * (W - PAD_L - PAD_R);
  const avMax = Math.max(100, Math.ceil(Math.max(...av.map((p) => p.v), 0) / 100) * 100);
  const yAv = (v: number) => PAD_Y + (1 - v / avMax) * (AV_H - 2 * PAD_Y);
  const yPr = (v: number) => PAD_Y + (1 - v / 100) * (PR_H - 2 * PAD_Y);

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = (((e.clientX - r.left) / r.width) * W - PAD_L) / (W - PAD_L - PAD_R) * end;
    setHover(Math.min(end, Math.max(0, t)));
  };
  const at = hover === null ? null : { av: nearest(av, hover), pr: nearest(pr, hover) };
  const hoverDeath = hover === null ? undefined : deaths.find((d) => Math.abs(d.t - hover) <= end / 60 + 0.5);

  const crosshair = (h: number) =>
    hover !== null && <line x1={x(hover)} x2={x(hover)} y1={0} y2={h} className={styles.chartCrosshair} />;

  return (
    <div className={styles.chart}>
      <p className={styles.chartReadout} aria-live="polite">
        {at
          ? [`第 ${Math.round(hover ?? 0)} 秒`, at.av && `行动值 ${at.av.v}`, at.pr && `进度 ${at.pr.v}%`,
             hoverDeath && '有角色阵亡'].filter(Boolean).join(' · ')
          : ''}
      </p>
      {av.length > 1 && (
        <figure className={styles.chartFigure}>
          <figcaption className={styles.chartLabel}>剩余行动值</figcaption>
          <svg viewBox={`0 0 ${W} ${AV_H}`} className={styles.chartSvg} onPointerMove={onMove} onPointerLeave={() => setHover(null)}
            role="img" aria-label={`剩余行动值从 ${av[0].v} 到 ${av[av.length - 1].v}`}>
            {[0, avMax / 2, avMax].map((v) => (
              <g key={v}>
                <line x1={PAD_L} x2={W - PAD_R} y1={yAv(v)} y2={yAv(v)} className={styles.chartGrid} />
                <text x={PAD_L - 6} y={yAv(v)} className={styles.chartTick} textAnchor="end" dominantBaseline="middle">{v}</text>
              </g>
            ))}
            <path d={path(av, x, yAv)} className={styles.chartLine} />
            {deaths.map((d, i) => {
              const v = d.av ?? nearest(av, d.t)?.v;
              return (
                <g key={i}>
                  <line x1={x(d.t)} x2={x(d.t)} y1={PAD_Y} y2={AV_H - PAD_Y} className={styles.chartDeathLine} />
                  {v !== undefined && <circle cx={x(d.t)} cy={yAv(v)} r={4} className={styles.chartDeath} />}
                  <text x={x(d.t) + 4} y={PAD_Y + 2} className={styles.chartTick} dominantBaseline="hanging">阵亡</text>
                </g>
              );
            })}
            {crosshair(AV_H)}
          </svg>
        </figure>
      )}
      {pr.length > 1 && (
        <figure className={styles.chartFigure}>
          <figcaption className={styles.chartLabel}>进度</figcaption>
          <svg viewBox={`0 0 ${W} ${PR_H}`} className={styles.chartSvg} onPointerMove={onMove} onPointerLeave={() => setHover(null)}
            role="img" aria-label={`进度从 ${pr[0].v}% 到 ${pr[pr.length - 1].v}%`}>
            {[0, 100].map((v) => (
              <g key={v}>
                <line x1={PAD_L} x2={W - PAD_R} y1={yPr(v)} y2={yPr(v)} className={styles.chartGrid} />
                <text x={PAD_L - 6} y={yPr(v)} className={styles.chartTick} textAnchor="end" dominantBaseline="middle">{v}%</text>
              </g>
            ))}
            <path d={path(pr, x, yPr)} className={styles.chartLine} />
            {crosshair(PR_H)}
          </svg>
        </figure>
      )}
      <div className={styles.chartAxis}>
        <span>0 秒</span>
        <span>{Math.round(end)} 秒</span>
      </div>
    </div>
  );
}
