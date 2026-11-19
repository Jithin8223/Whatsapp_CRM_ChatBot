import { create } from 'zustand';
import { api, initializeCsrf } from './api.js';

let bootstrapRequest;

export const useAuth = create((set, get) => ({
  user: null, workspaces: [], workspaceId: '', ready: false, revision: 0, realtime: 'connecting',
  bootstrap: () => {
    bootstrapRequest ??= initializeCsrf().then(() => get().loadMe())
      .catch(error => { if (error.response?.status !== 401) throw error; })
      .finally(() => { bootstrapRequest = undefined; set({ ready: true }); });
    return bootstrapRequest;
  },
  loadMe: async () => {
    const { data } = await api.get('/auth/me/');
    const saved = localStorage.getItem('crm_workspace');
    const current = data.workspaces.find(w => w.id === (get().workspaceId || saved)) || data.workspaces[0];
    set({ ...data, workspaceId: current?.id || '' });
  },
  login: async (values, registering = false) => {
    await initializeCsrf();
    await api.post(registering ? '/auth/register/' : '/auth/login/', values);
    await initializeCsrf();
    await get().loadMe();
  },
  logout: async () => {
    await api.post('/auth/logout/');
    set({ user: null, workspaces: [], workspaceId: '', revision: 0 });
  },
  selectWorkspace: id => { localStorage.setItem('crm_workspace', id); set(s => ({ workspaceId: id, revision: s.revision + 1 })); },
  refresh: () => set(s => ({ revision: s.revision + 1 })),
}));

api.interceptors.response.use(response => response, error => {
  if (error.response?.status === 401 && !['/auth/login/', '/auth/register/'].includes(error.config?.url)) {
    useAuth.setState({ user: null, workspaces: [], workspaceId: '' });
  }
  return Promise.reject(error);
});
