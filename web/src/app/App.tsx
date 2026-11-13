import { BookOpen, GitCompareArrows, History, MonitorPlay, Settings2 } from 'lucide-react';
import { useEffect, useState, type ComponentType } from 'react';

import { api, type KnowledgeMeta } from '@/lib/api';
import { ToastHost } from '@/lib/toast';
import KnowledgePage from '@/pages/knowledge/KnowledgePage';
import Placeholder from '@/pages/Placeholder';
import SettingsPage from '@/pages/settings/SettingsPage';

import styles from './shell.module.css';

type Page = { path: string; label: string; icon: ComponentType<{ size?: number; strokeWidth?: number }>; soon?: boolean };
const GROUPS: { label: string; pages: Page[] }[] = [
  {
    label: '对局',
    pages: [
      { path: '#/watch', label: '观战', icon: MonitorPlay, soon: true },
      { path: '#/runs', label: '对局记录', icon: History, soon: true },
      { path: '#/compare', label: '模型对比', icon: GitCompareArrows, soon: true },
    ],
  },
  {
    label: '数据',
    pages: [
      { path: '#/knowledge', label: '知识库', icon: BookOpen },
      { path: '#/settings', label: '设置', icon: Settings2 },
    ],
  },
];
const ALL = GROUPS.flatMap((g) => g.pages);

const PLACEHOLDERS: Record<string, { title: string; description: string }> = {
  '#/watch': { title: '观战', description: '实时观看 agent 对局:游戏截图、局面、模型的思考和每一步动作。全自动对局完成后开放。' },
  '#/runs': { title: '对局记录', description: '历史对局列表和逐步回放,能看到每一步的提示词、思考和结果。决策层完成后开放。' },
  '#/compare': { title: '模型对比', description: '把同一批局面交给多个模型,并排比较它们的思考与决策。离线回放完成后开放。' },
};

function readPath() {
  const hash = window.location.hash.split('?')[0];
  return ALL.some((p) => p.path === hash) ? hash : '#/knowledge';
}

export default function App() {
  const [path, setPath] = useState('#/knowledge');
  const [status, setStatus] = useState<{ knowledge: KnowledgeMeta; models: number } | null>(null);

  useEffect(() => {
    setPath(readPath());
    const onHash = () => setPath(readPath());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    api<{ knowledge: KnowledgeMeta; models: number }>('/api/status').then(setStatus).catch(() => setStatus(null));
  }, [path]);

  const placeholder = PLACEHOLDERS[path];
  return (
    <ToastHost>
      <div className={styles.shell}>
        <aside className={styles.sidebar}>
          <div className={styles.brand}>
            <span className={styles.brandName}>货币战争</span>
            <span className={styles.brandSub}>入画奇航 · LLM 游玩 agent</span>
          </div>
          <nav className={styles.nav} aria-label="主导航">
            {GROUPS.map((g) => (
              <div key={g.label} className={styles.navGroup}>
                <span className={styles.navLabel}>{g.label}</span>
                {g.pages.map(({ path: p, label, icon: Icon, soon }) => (
                  <a key={p} href={p} className={styles.navItem} aria-current={p === path ? 'page' : undefined}>
                    <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
                    {label}
                    {soon && <span className={styles.navSoon}>规划中</span>}
                  </a>
                ))}
              </div>
            ))}
          </nav>
          <div className={styles.status}>
            <div className={styles.statusLine}>
              <span>知识库</span>
              <span className={styles.statusValue}>
                {status?.knowledge.synced_at ? `S${status.knowledge.season_id} · ${status.knowledge.game_version}` : '未同步'}
              </span>
            </div>
            <div className={styles.statusLine}>
              <span>模型档案</span>
              <span className={styles.statusValue}>{status ? status.models : '–'}</span>
            </div>
          </div>
        </aside>
        <main className={styles.main}>
          {path === '#/settings' && <SettingsPage />}
          {path === '#/knowledge' && <KnowledgePage />}
          {placeholder && <Placeholder {...placeholder} />}
        </main>
      </div>
    </ToastHost>
  );
}
