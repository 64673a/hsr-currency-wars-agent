// 知识库条目的详情渲染(抽屉内容),每类一个。
import { Alert, Badge, Tag } from '@arclux/arc-ui-react';
import type { ReactNode } from 'react';

import type { Item } from '../api';

function Tags({ items, variant = 'default' }: { items?: string[]; variant?: any }) {
  if (!items?.length) return null;
  return (
    <span className="row">
      {items.map((t) => (
        <Tag key={t} size="sm" variant={variant}>
          {t}
        </Tag>
      ))}
    </span>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <span>{label}</span>
      <span>{children}</span>
    </>
  );
}

function Effects({ d }: { d: Item }) {
  return (
    <>
      {d.flow && <Alert variant="warning" density="compact">会改变对局流程或让既定计划失效,决策时需要特别考虑</Alert>}
      {d.effects?.length > 0 && (
        <div className="kv">
          <Field label="效果标签">
            <Tags items={d.effects} variant={d.flow ? 'warning' : 'default'} />
          </Field>
        </div>
      )}
    </>
  );
}

function Header({ d, sub }: { d: Item; sub?: ReactNode }) {
  return (
    <div className="row" style={{ marginBottom: 12 }}>
      {d.icon && <img className="icon lg" src={d.icon} alt="" />}
      <div>
        <div style={{ fontSize: 18, fontWeight: 600 }}>{d.name ?? d.title}</div>
        {sub && <div className="muted">{sub}</div>}
      </div>
    </div>
  );
}

function Role({ d }: { d: Item }) {
  return (
    <>
      <Header d={d} sub={`${d.cost} 费 · ${d.position}${d.hidden ? ' · 隐藏' : ''}`} />
      <div className="kv">
        <Field label="羁绊">
          <Tags items={d.traits.map((t: any) => t.name)} variant="primary" />
        </Field>
        <Field label="定位">
          <Tags items={d.tags} />
        </Field>
      </div>
      {d.skills.map((s: any) => (
        <div key={s.position} className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>
            {s.position}:{s.name} <Tags items={s.tags} />
          </h3>
          {s.stars.map((st: any) => (
            <div key={st.star} className="layer">
              <b>{st.star}★</b>
              <span className="desc">{st.desc}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  );
}

function Trait({ d }: { d: Item }) {
  return (
    <>
      <Header d={d} sub={`${d.type}羁绊 · ${d.members.length} 名成员`} />
      {d.unused && <Alert variant="info" density="compact">本赛季没有角色拥有这个羁绊</Alert>}
      {d.desc && <p className="desc">{d.desc}</p>}
      {d.layers.length > 0 && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>档位</h3>
          {d.layers.map((l: any) => (
            <div key={l.count} className="layer">
              <b>{l.count}</b>
              <span className="desc">{l.desc}</span>
            </div>
          ))}
        </div>
      )}
      {d.remarks.length > 0 && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>术语</h3>
          {d.remarks.map((r: string) => (
            <p key={r} className="desc muted">
              {r}
            </p>
          ))}
        </div>
      )}
      <div className="section">
        <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>成员</h3>
        <Tags items={d.members.map((m: any) => `${m.name}(${m.cost}费)`)} />
      </div>
    </>
  );
}

function Equipment({ d }: { d: Item }) {
  return (
    <>
      <Header d={d} sub={d.category + (d.intrinsic_role ? ` · ${d.intrinsic_role}的固有装备` : '')} />
      {d.properties.length > 0 && (
        <div className="kv">
          {d.properties.map((p: any) => (
            <Field key={p.name} label={p.name}>
              {p.value}
            </Field>
          ))}
        </div>
      )}
      {d.desc && <p className="desc">{d.desc}</p>}
      {d.recipes.length > 0 && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>配方</h3>
          {d.recipes.map((r: any, i: number) => (
            <div key={i}>
              {r.a_name} + {r.b_name}
            </div>
          ))}
        </div>
      )}
      {d.used_in.length > 0 && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>可以合成</h3>
          {d.used_in.map((u: any, i: number) => (
            <div key={i}>
              + {u.with_name} → <b>{u.name}</b>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function Simple({ d, sub }: { d: Item; sub?: string }) {
  return (
    <>
      <Header d={d} sub={sub} />
      <Effects d={d} />
      {d.desc && <p className="desc">{d.desc}</p>}
      {d.suitable_roles?.length > 0 && (
        <div className="kv">
          <Field label="适用角色">
            <Tags items={d.suitable_roles.map((r: any) => r.name)} />
          </Field>
        </div>
      )}
      {d.tags?.length > 0 && (
        <div className="kv">
          <Field label="标签">
            <Tags items={d.tags} />
          </Field>
        </div>
      )}
    </>
  );
}

function Companion({ d }: { d: Item }) {
  return (
    <>
      <Header d={d} sub={[d.type, d.origin, d.verified ? '已核实' : '未核实', d.seen_count ? `遇到 ${d.seen_count} 次` : ''].filter(Boolean).join(' · ')} />
      <Effects d={d} />
      {d.desc ? <p className="desc">{d.desc}</p> : <p className="muted">还没有记录游戏内的描述。</p>}
      {d.flow_effects?.length > 0 && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>对流程的影响</h3>
          {d.flow_effects.map((f: string) => (
            <div key={f}>· {f}</div>
          ))}
        </div>
      )}
      <p className="muted">来源:{d.source}</p>
    </>
  );
}

function Board({ units }: { units: any[] }) {
  return (
    <div className="board">
      {units.map((u, i) =>
        typeof u === 'string' ? (
          <div key={i} className="unit">
            {u}
          </div>
        ) : (
          <div key={i} className={'unit' + (u.carry ? ' carry' : '')}>
            <b>{u.name}</b> {u.star > 1 && <span>{u.star}★</span>} <span className="muted">{u.cost}费</span>
            {u.carry && (
              <Badge size="sm" variant="primary">
                主C
              </Badge>
            )}
            {u.equipment?.length > 0 && <div className="muted">{u.equipment.join('、')}</div>}
            {u.spotlights?.length > 0 && <div className="muted">光环:{u.spotlights.join('、')}</div>}
          </div>
        ),
      )}
    </div>
  );
}

function Guide({ d }: { d: Item }) {
  return (
    <>
      <Header d={{ ...d, name: d.title }} sub={`使用 ${d.use_count.toLocaleString()} 次 · 作者 ${d.author}${d.support_hard ? ' · 适合高难' : ''}`} />
      <div className="kv">
        <Field label="主C">{d.carries.join('、') || '—'}</Field>
        <Field label="核心羁绊">
          <Tags items={d.key_traits.map((t: any) => `${t.count} ${t.name}`)} variant="primary" />
        </Field>
        <Field label="节奏">
          <Tags items={d.tempo_labels} />
        </Field>
        <Field label="适配环境">{d.environments.join('、') || '—'}</Field>
        <Field label="推荐策略">{d.recommended_augments.join('、') || '—'}</Field>
        <Field label="装备顺序">
          {[...d.equip_order.compose].join(' → ') || '—'}
        </Field>
        <Field label="攻略码">
          <span className="mono">{d.share_code}</span>
        </Field>
      </div>
      {d.description && (
        <div className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>作者思路</h3>
          <p className="desc">{d.description}</p>
        </div>
      )}
      <div className="section">
        <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>成型阵容 · 前台</h3>
        <Board units={d.final_board.front} />
        <h3 style={{ margin: '12px 0 6px', fontSize: 15 }}>成型阵容 · 后台</h3>
        <Board units={d.final_board.back} />
      </div>
      {(['early', 'middle'] as const).map((k) => (
        <div key={k} className="section">
          <h3 style={{ margin: '0 0 6px', fontSize: 15 }}>{k === 'early' ? '前期示例' : '中期示例'}</h3>
          <Board units={[...d.example_boards[k].front, ...d.example_boards[k].back]} />
        </div>
      ))}
    </>
  );
}

export function Detail({ kind, d }: { kind: string; d: Item }) {
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
      return <pre className="block">{JSON.stringify(d, null, 2)}</pre>;
  }
}

// 列表行的副标题
export function summaryLine(kind: string, it: Item): string {
  switch (kind) {
    case 'roles':
      return `${it.cost} 费 · ${it.position} · ${it.traits.join('、')}`;
    case 'traits':
      return `${it.type} · ${it.members} 名成员` + (it.thresholds?.length ? ` · 档位 ${it.thresholds.join('/')}` : '');
    case 'equipment':
      return it.category + (it.recipes?.length ? ` · ${it.recipes.join(' 或 ')}` : '');
    case 'augments':
      return `${it.quality} · ${it.desc}`;
    case 'guides':
      return `使用 ${Number(it.use_count).toLocaleString()} 次 · ${it.key_traits.join(' ')} · 主C ${it.carries.join('、')}`;
    default:
      return it.desc ?? '';
  }
}
