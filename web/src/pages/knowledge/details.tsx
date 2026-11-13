// 知识库条目的列表徽标、摘要行和详情(抽屉内容)。
import type { ReactNode } from 'react';

import { Alert } from '@/components/arc/alert/alert';
import { Badge, type BadgeColor } from '@/components/arc/badge/badge';
import { CopyButton } from '@/components/arc/copy-button/copy-button';
import type { Item } from '@/lib/api';

import styles from './knowledge.module.css';

// 费用沿用游戏里的颜色梯度:白、绿、蓝、紫、金,7 费阿哈单独一色
const COST_COLOR: Record<number, BadgeColor | undefined> = { 2: 'teal', 3: 'sky', 4: 'violet', 5: 'amber', 7: 'rose' };
const QUALITY_COLOR: Record<string, BadgeColor | undefined> = { 金色: 'amber', 棱彩: 'orchid' };

function Chips({ items, color }: { items?: string[]; color?: BadgeColor }) {
  if (!items?.length) return null;
  return (
    <span className={styles.chips}>
      {items.map((t) => (
        <Badge key={t} size="sm" color={color}>
          {t}
        </Badge>
      ))}
    </span>
  );
}

export function RowBadges({ kind, it }: { kind: string; it: Item }) {
  return (
    <>
      {kind === 'roles' && (
        <Badge size="sm" color={COST_COLOR[it.cost]}>
          {it.cost} 费
        </Badge>
      )}
      {kind === 'augments' && (
        <Badge size="sm" color={QUALITY_COLOR[it.quality]}>
          {it.quality}
        </Badge>
      )}
      {it.flow && (
        <Badge size="sm" tone="warning">
          改变流程
        </Badge>
      )}
      {it.support_hard && (
        <Badge size="sm" tone="info">
          适合高难
        </Badge>
      )}
      {it.unused && <Badge size="sm">本赛季未使用</Badge>}
      {kind === 'companions' && !it.verified && <Badge size="sm">未核实</Badge>}
    </>
  );
}

export function summaryLine(kind: string, it: Item): string {
  switch (kind) {
    case 'roles':
      return `${it.position} · ${it.traits.join('、')}`;
    case 'traits':
      return `${it.type}羁绊 · ${it.members} 名成员` + (it.thresholds?.length ? ` · ${it.thresholds.join(' / ')} 人激活` : '');
    case 'equipment':
      return it.category + (it.recipes?.length ? ` · ${it.recipes.join(' 或 ')}` : '');
    case 'guides':
      return `${Number(it.use_count).toLocaleString('zh-CN')} 人使用 · ${it.key_traits.join(' ')}` + (it.carries.length ? ` · 主 C ${it.carries.join('、')}` : '');
    default:
      return it.desc ?? '';
  }
}

function Head({ d, sub }: { d: Item; sub: ReactNode }) {
  return (
    <div className={styles.detailHead}>
      {d.icon && <img className={styles.detailIcon} src={d.icon} alt="" />}
      <div>
        <h3 className={styles.detailName}>{d.name ?? d.title}</h3>
        <p className={styles.detailSub}>{sub}</p>
      </div>
    </div>
  );
}

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.block}>
      <h4 className={styles.blockTitle}>{title}</h4>
      {children}
    </section>
  );
}

function Text({ children }: { children: ReactNode }) {
  return <p className={styles.text}>{children}</p>;
}

function Flow({ d }: { d: Item }) {
  if (!d.flow && !d.effects?.length) return null;
  return (
    <>
      {d.flow && (
        <Alert tone="warning" title="会改变对局流程">
          拿到它之后节点结构、商店概率或阵容规划可能失效,agent 会据此重新规划。
        </Alert>
      )}
      {d.effects?.length > 0 && (
        <Block title="效果标签">
          <Chips items={d.effects} />
        </Block>
      )}
    </>
  );
}

function Role({ d }: { d: Item }) {
  return (
    <>
      <Head d={d} sub={`${d.cost} 费 · ${d.position}${d.hidden ? ' · 隐藏角色' : ''}`} />
      <dl className={styles.kv}>
        <dt>羁绊</dt>
        <dd>
          <Chips items={d.traits.map((t: any) => t.name)} color="violet" />
        </dd>
        <dt>定位</dt>
        <dd>{d.tags.join('、') || '—'}</dd>
      </dl>
      {d.skills.map((s: any) => (
        <Block key={s.position} title={`${s.position}技能:${s.name}`}>
          <div className={styles.steps}>
            {s.stars.map((st: any) => (
              <div key={st.star} className={styles.step}>
                <span className={styles.stepKey}>{st.star} 星</span>
                <span>{st.desc}</span>
              </div>
            ))}
          </div>
        </Block>
      ))}
    </>
  );
}

function Trait({ d }: { d: Item }) {
  return (
    <>
      <Head d={d} sub={`${d.type}羁绊 · ${d.members.length} 名成员`} />
      {d.unused && <Alert tone="info" title="本赛季没有角色拥有这个羁绊" />}
      {d.desc && <Text>{d.desc}</Text>}
      {d.layers.length > 0 && (
        <Block title="档位">
          <div className={styles.steps}>
            {d.layers.map((l: any) => (
              <div key={l.count} className={styles.step}>
                <span className={styles.stepKey}>{l.count} 人</span>
                <span>{l.desc}</span>
              </div>
            ))}
          </div>
        </Block>
      )}
      {d.remarks.length > 0 && (
        <Block title="术语">
          {d.remarks.map((r: string) => (
            <Text key={r}>{r}</Text>
          ))}
        </Block>
      )}
      {d.members.length > 0 && (
        <Block title="成员">
          <Chips items={d.members.map((m: any) => `${m.name} · ${m.cost} 费`)} />
        </Block>
      )}
    </>
  );
}

function Equipment({ d }: { d: Item }) {
  return (
    <>
      <Head d={d} sub={d.category + (d.intrinsic_role ? ` · ${d.intrinsic_role}的固有装备` : '')} />
      {d.properties.length > 0 && (
        <dl className={styles.kv}>
          {d.properties.map((p: any) => (
            <div key={p.name} style={{ display: 'contents' }}>
              <dt>{p.name}</dt>
              <dd>{p.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {d.desc && <Text>{d.desc}</Text>}
      {d.recipes.length > 0 && (
        <Block title="配方">
          {d.recipes.map((r: any, i: number) => (
            <Text key={i}>
              {r.a_name} + {r.b_name}
            </Text>
          ))}
        </Block>
      )}
      {d.used_in.length > 0 && (
        <Block title="可以合成">
          <div className={styles.steps}>
            {d.used_in.map((u: any, i: number) => (
              <div key={i} className={styles.step}>
                <span className={styles.stepKey}>+</span>
                <span>
                  {u.with_name} → {u.name}
                </span>
              </div>
            ))}
          </div>
        </Block>
      )}
    </>
  );
}

function Simple({ d, sub }: { d: Item; sub: string }) {
  return (
    <>
      <Head d={d} sub={sub} />
      {d.desc && <Text>{d.desc}</Text>}
      <Flow d={d} />
      {d.suitable_roles?.length > 0 && (
        <Block title="适用角色">
          <Chips items={d.suitable_roles.map((r: any) => r.name)} />
        </Block>
      )}
      {d.tags?.length > 0 && (
        <Block title="标签">
          <Chips items={d.tags} />
        </Block>
      )}
    </>
  );
}

function Companion({ d }: { d: Item }) {
  const sub = [d.type, d.origin === '本机' ? '本机记录' : '公共档案', d.verified ? '已核实' : '未核实', d.seen_count ? `遇到 ${d.seen_count} 次` : '']
    .filter(Boolean)
    .join(' · ');
  return (
    <>
      <Head d={d} sub={sub} />
      {d.desc ? <Text>{d.desc}</Text> : <Text>还没有记录游戏内的描述,下次在对局中遇到时会自动补上。</Text>}
      {d.flow_effects?.length > 0 && (
        <Block title="对流程的影响">
          {d.flow_effects.map((f: string) => (
            <Text key={f}>{f}</Text>
          ))}
        </Block>
      )}
      <Block title="来源">
        <Text>{d.source}</Text>
      </Block>
    </>
  );
}

function Board({ units }: { units: any[] }) {
  if (!units.length) return <Text>—</Text>;
  return (
    <div className={styles.board}>
      {units.map((u, i) =>
        typeof u === 'string' ? (
          <span key={i} className={styles.unit}>
            {u}
          </span>
        ) : (
          <span key={i} className={`${styles.unit} ${u.carry ? styles.unitCarry : ''}`}>
            <span>
              {u.name}
              {u.star > 1 ? ` · ${u.star} 星` : ''}
            </span>
            <span className={styles.unitMeta}>
              {u.cost} 费{u.carry ? ' · 主 C' : ''}
            </span>
            {u.equipment?.length > 0 && <span className={styles.unitMeta}>{u.equipment.join('、')}</span>}
          </span>
        ),
      )}
    </div>
  );
}

function Guide({ d }: { d: Item }) {
  return (
    <>
      <Head d={{ ...d, name: d.title }} sub={`${d.use_count.toLocaleString('zh-CN')} 人使用 · 作者 ${d.author}`} />
      <dl className={styles.kv}>
        <dt>主 C</dt>
        <dd>{d.carries.join('、') || '—'}</dd>
        <dt>核心羁绊</dt>
        <dd>
          <Chips items={d.key_traits.map((t: any) => `${t.count} ${t.name}`)} color="violet" />
        </dd>
        <dt>节奏</dt>
        <dd>{d.tempo_labels.join('、') || '—'}</dd>
        <dt>适配环境</dt>
        <dd>{d.environments.join('、') || '—'}</dd>
        <dt>推荐策略</dt>
        <dd>{d.recommended_augments.join('、') || '—'}</dd>
        <dt>合成顺序</dt>
        <dd>{d.equip_order.compose.join(' → ') || '—'}</dd>
        <dt>攻略码</dt>
        <dd>{d.share_code ? <CopyButton value={d.share_code} label={d.share_code} /> : '—'}</dd>
      </dl>
      {d.description && (
        <Block title="作者思路">
          <Text>{d.description}</Text>
        </Block>
      )}
      <Block title="成型阵容 · 前台">
        <Board units={d.final_board.front} />
      </Block>
      <Block title="成型阵容 · 后台">
        <Board units={d.final_board.back} />
      </Block>
      <Block title="前期示例">
        <Board units={[...d.example_boards.early.front, ...d.example_boards.early.back]} />
      </Block>
      <Block title="中期示例">
        <Board units={[...d.example_boards.middle.front, ...d.example_boards.middle.back]} />
      </Block>
    </>
  );
}

export function Detail({ kind, d }: { kind: string; d: Item }) {
  const body = (() => {
    switch (kind) {
      case 'roles':
        return <Role d={d} />;
      case 'traits':
        return <Trait d={d} />;
      case 'equipment':
        return <Equipment d={d} />;
      case 'augments':
        return <Simple d={d} sub={`${d.quality_name}投资策略`} />;
      case 'portals':
        return <Simple d={d} sub="投资环境" />;
      case 'spotlights':
        return <Simple d={d} sub="主角光环" />;
      case 'companions':
        return <Companion d={d} />;
      case 'guides':
        return <Guide d={d} />;
      default:
        return null;
    }
  })();
  return <div className={styles.detail}>{body}</div>;
}
