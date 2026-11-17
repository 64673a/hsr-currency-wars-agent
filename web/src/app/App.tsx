import { BookOpen, History, MonitorPlay, Settings2 } from 'lucide-react';
import { useEffect, useState, type ComponentType } from 'react';

import SegmentedControl from '@/components/arc/segmented-control/segmented-control';
import { api, type KnowledgeMeta } from '@/lib/api';
import { ToastHost } from '@/lib/toast';
import KnowledgePage from '@/pages/knowledge/KnowledgePage';
import PlayPage from '@/pages/play/PlayPage';
import RunsPage from '@/pages/runs/RunsPage';
import SettingsPage from '@/pages/settings/SettingsPage';

import styles from './shell.module.css';

type Page = { path: string; label: string; icon: ComponentType<{ size?: number; strokeWidth?: number }> };
const ALL: Page[] = [
  { path: '#/play', label: '对局', icon: MonitorPlay },
  { path: '#/runs', label: '对局记录', icon: History },
  { path: '#/knowledge', label: '知识库', icon: BookOpen },
  { path: '#/settings', label: '设置', icon: Settings2 },
];

const THEMES = [
  { value: 'light', label: '日间' },
  { value: 'dark', label: '夜间' },
];

// 主题只存在这个浏览器里;index.html 在渲染前已经按上次的选择设好了 data-theme
function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme ?? 'light');
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('cw-theme', theme);
    } catch {
      // 隐私模式下存不了,只影响下次打开
    }
  }, [theme]);
  return [theme, setTheme] as const;
}

function readPath() {
  const hash = window.location.hash.split('?')[0];
  return ALL.some((p) => p.path === hash) ? hash : '#/play';
}

export default function App() {
  const [path, setPath] = useState('#/play');
  const [status, setStatus] = useState<{ knowledge: KnowledgeMeta; models: number } | null>(null);
  const [theme, setTheme] = useTheme();

  useEffect(() => {
    setPath(readPath());
    const onHash = () => setPath(readPath());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  useEffect(() => {
    api<{ knowledge: KnowledgeMeta; models: number }>('/api/status').then(setStatus).catch(() => setStatus(null));
  }, [path]);

  return (
    <ToastHost>
      <div className={styles.shell}>
        <aside className={styles.sidebar}>
          <div className={styles.brand}>
            <span className={styles.brandName}>货币战争</span>
          </div>
          <nav className={styles.nav} aria-label="主导航">
            {ALL.map(({ path: p, label, icon: Icon }) => (
              <a key={p} href={p} className={styles.navItem} aria-current={p === path ? 'page' : undefined}>
                <Icon size={16} strokeWidth={1.75} aria-hidden="true" />
                {label}
              </a>
            ))}
          </nav>
          <SegmentedControl label="外观" options={THEMES} value={theme} onValueChange={setTheme} fullWidth className={styles.theme} />
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
          {path === '#/play' && <PlayPage />}
          {path === '#/runs' && <RunsPage />}
        </main>
      </div>
    </ToastHost>
  );
}
