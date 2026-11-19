import { useEffect, useState, useCallback, useRef } from 'react';
import { api, errorText, retryAfter } from './api.js';
import { useAuth } from './auth.js';

export function useResource(path, pollMs = 0) {
  const revision = useAuth(s => s.revision);
  const [state, setState] = useState({ data: null, loading: true, error: '' });
  const lastRequest = useRef({ path: null, retryAt: 0 });
  useEffect(() => {
    if (!path) { setState({ data: null, loading: false, error: '' }); return; }
    const controller = new AbortController();
    if (lastRequest.current.path !== path) lastRequest.current = { path, retryAt: 0 };
    const request = lastRequest.current;
    let timer;
    async function read(attempt = 0) {
      setState(old => ({ path, data: old.path === path ? old.data : null, loading: true, error: '' }));
      try {
        const { data } = await api.get(path, { signal: controller.signal });
        if (controller.signal.aborted) return;
        request.retryAt = 0;
        setState({ path, data, loading: false, error: '' });
        if (pollMs && data?.state !== 'connected') timer = setTimeout(read, pollMs);
      } catch (error) {
        if (controller.signal.aborted) return;
        const status = error.response?.status;
        const retryable = !status || [429, 502, 503, 504].includes(status);
        const delay = Math.max(retryAfter(error.response?.headers), 1000 * 2 ** attempt);
        if (retryable) request.retryAt = Date.now() + delay;
        setState(old => ({ ...old, loading: false, error: errorText(error) }));
        if (retryable && attempt < 2 && delay <= 30000) timer = setTimeout(() => read(attempt + 1), delay);
      }
    }
    const wait = Math.max(0, request.retryAt - Date.now());
    if (wait <= 30000) timer = setTimeout(read, wait);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [path, revision, pollMs]);
  return state.path === path ? state : { data: null, loading: Boolean(path), error: '' };
}

export function useWorkspacePath() {
  const id = useAuth(s => s.workspaceId);
  return `/workspaces/${id}/`;
}

export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  const run = useCallback(async callback => {
    if (inFlight.current) return null;
    inFlight.current = true;
    setBusy(true); setError('');
    try { const result = await callback(); useAuth.getState().refresh(); return result; }
    catch (error) { setError(errorText(error)); return null; }
    finally { inFlight.current = false; setBusy(false); }
  }, []);
  return { busy, error, run };
}

export function useRealtime() {
  const id = useAuth(s => s.workspaceId);
  const user = useAuth(s => s.user);
  useEffect(() => {
    if (!id || !user) return;
    let socket, timer, refreshTimer, stopped = false, failures = 0;
    const refresh = () => {
      refreshTimer ??= setTimeout(() => {
        refreshTimer = undefined;
        useAuth.getState().refresh();
      }, 200);
    };
    const connect = () => {
      useAuth.setState({ realtime: 'connecting' });
      socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/workspaces/${id}/`);
      socket.onopen = () => { useAuth.setState({ realtime: 'live' }); refresh(); };
      socket.onmessage = event => {
        try {
          const value = JSON.parse(event.data);
          if (value.kind === 'heartbeat') failures = 0;
          if (value.kind === 'team.updated') useAuth.getState().loadMe().catch(() => {});
          if (value.kind === 'lead.created') useAuth.setState(state => ({ leadAlerts: (state.leadAlerts || 0) + 1 }));
          if (!['ready', 'heartbeat', 'pong'].includes(value.kind)) refresh();
        } catch { /* REST remains authoritative. */ }
      };
      socket.onclose = event => {
        if (stopped) return;
        if ([1000, 4400, 4401].includes(event.code) || failures >= 5) {
          useAuth.setState({ realtime: 'offline' });
          if (event.code === 4401) useAuth.getState().loadMe().catch(() => {});
          return;
        }
        useAuth.setState({ realtime: 'reconnecting' });
        timer = setTimeout(connect, Math.min(1000 * 2 ** failures++, 15000));
      };
    };
    // StrictMode immediately tears down its first effect before this timer runs.
    timer = setTimeout(connect, 0);
    const fallback = setInterval(() => { if (socket?.readyState !== WebSocket.OPEN) refresh(); }, 15000);
    return () => {
      stopped = true;
      clearTimeout(timer);
      clearTimeout(refreshTimer);
      clearInterval(fallback);
      if (socket) {
        socket.onopen = socket.onmessage = socket.onclose = null;
        socket.close(1000);
      }
    };
  }, [id, user?.id]);
}
