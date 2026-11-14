import { useEffect, useRef, useState } from 'react';

import { ApiError } from '@/lib/api';

import styles from './play.module.css';

// 游戏实时画面:WebRTC 拉流,信令走 WHEP(POST 本端 SDP,拿回服务端 SDP),由后端转发给本机的 MediaMTX

type LiveState = 'connecting' | 'playing' | 'nogame' | 'retry';

const MESSAGES: Record<Exclude<LiveState, 'playing'>, string> = {
  connecting: '正在连接实时画面',
  nogame: '没有游戏画面',
  retry: '实时画面断开,正在重连',
};

function waitIceGathering(pc: RTCPeerConnection, ms: number) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise<void>((resolve) => {
    const t = window.setTimeout(resolve, ms);
    pc.addEventListener('icegatheringstatechange', () => {
      if (pc.iceGatheringState === 'complete') {
        window.clearTimeout(t);
        resolve();
      }
    });
  });
}

async function connect(video: HTMLVideoElement) {
  const pc = new RTCPeerConnection();
  pc.addTransceiver('video', { direction: 'recvonly' });
  pc.ontrack = (e) => {
    video.srcObject = e.streams[0] ?? new MediaStream([e.track]);
  };
  try {
    await pc.setLocalDescription(await pc.createOffer());
    // 局域网里本机候选地址很快就收集完,一次性带上,不做增量交换
    await waitIceGathering(pc, 1000);
    const resp = await fetch('/api/live/whep', {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp' },
      body: pc.localDescription!.sdp,
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new ApiError(resp.status, data.detail ?? resp.statusText);
    }
    await pc.setRemoteDescription({ type: 'answer', sdp: await resp.text() });
    return { pc, session: resp.headers.get('Location') };
  } catch (e) {
    pc.close();
    throw e;
  }
}

export function LiveView() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState<LiveState>('connecting');

  useEffect(() => {
    const video = videoRef.current!;
    let stopped = false;
    let pc: RTCPeerConnection | null = null;
    let session: string | null = null;
    let timer = 0;

    const release = () => {
      pc?.close();
      pc = null;
      video.srcObject = null;
      if (session) fetch(session, { method: 'DELETE', keepalive: true }).catch(() => {});
      session = null;
    };
    const retry = (next: LiveState, ms: number) => {
      release();
      setState(next);
      window.clearTimeout(timer);
      timer = window.setTimeout(start, ms);
    };
    async function start() {
      if (stopped || document.hidden || pc) return;
      try {
        const r = await connect(video);
        if (stopped || document.hidden) {
          r.pc.close();
          if (r.session) fetch(r.session, { method: 'DELETE', keepalive: true }).catch(() => {});
          return;
        }
        pc = r.pc;
        session = r.session;
        const current = pc;
        current.onconnectionstatechange = () => {
          if (current !== pc) return;
          if (['failed', 'disconnected', 'closed'].includes(current.connectionState)) retry('retry', 2000);
        };
      } catch (e) {
        retry(e instanceof ApiError && e.status === 404 ? 'nogame' : 'retry', 5000);
      }
    }

    // 页面切到后台时断开,回来再连,没人看时服务端会停止抓取和编码
    const onVisibility = () => {
      if (document.hidden) {
        window.clearTimeout(timer);
        release();
      } else {
        setState('connecting');
        start();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    start();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      release();
    };
  }, []);

  return (
    <figure className={styles.shot}>
      <video ref={videoRef} className={styles.video} autoPlay muted playsInline onPlaying={() => setState('playing')}
        aria-label="游戏实时画面" />
      {state !== 'playing' && <span className={styles.noShot}>{MESSAGES[state]}</span>}
    </figure>
  );
}
