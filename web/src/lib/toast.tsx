// 后台任务完成时的提示(前台操作的结果在原地确认,不用提示条)。
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';

import Toast from '@/components/arc/toast/toast';

type Notice = { id: number; title: string; description?: string };
const ToastContext = createContext<(title: string, description?: string) => void>(() => {});

export function ToastHost({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null);
  const show = useCallback((title: string, description?: string) => setNotice({ id: Date.now(), title, description }), []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {notice && (
        <Toast key={notice.id} title={notice.title} description={notice.description} open onOpenChange={(open) => !open && setNotice(null)} />
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
