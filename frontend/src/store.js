import { create } from 'zustand';
import { api } from './api.js';

export const useInfrastructure = create((set, get) => ({
  data: null, loading: false, error: '', updated: null,
  refresh: async () => {
    if (get().loading) return;
    set({ loading: true, error: '' });
    try {
      const response = await api.get('/health/infrastructure/', { validateStatus: status => status === 200 || status === 503 });
      set({ data: response.data, updated: new Date(), loading: false });
    } catch {
      set({ data: null, loading: false, error: 'Unable to reach the infrastructure API. Check the backend container and try again.' });
    }
  },
}));
