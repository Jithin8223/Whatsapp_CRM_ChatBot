import axios from 'axios';

export const api = axios.create({ baseURL: '/api', timeout: 10000 });

let csrfToken = '';
export async function initializeCsrf() {
  const response = await api.get('/auth/csrf/');
  csrfToken = response.data.csrfToken;
}
api.interceptors.request.use(config => {
  if (!['get', 'head', 'options'].includes(config.method?.toLowerCase())) config.headers['X-CSRFToken'] = csrfToken;
  return config;
});
export function errorText(error) {
  if (!error.response) return 'Unable to reach the server. Check your connection and retry.';
  if (error.response.status === 429) {
    const delay = retryAfter(error.response.headers);
    return delay ? `Too many requests. Try again in ${Math.ceil(delay / 1000)} seconds.` : 'Too many requests. Try again shortly.';
  }
  const data = error.response.data;
  if (typeof data?.detail === 'string') return data.detail;
  if (data && typeof data === 'object') return Object.entries(data).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(' ') : typeof value === 'string' ? value : 'Check this field.'}`).join(' ');
  return 'The request could not be completed. Please retry.';
}

export function retryAfter(headers) {
  const value = headers?.['retry-after'];
  if (value == null) return 0;
  const delay = /^\d+$/.test(value) ? Number(value) * 1000 : Date.parse(value) - Date.now();
  return Number.isFinite(delay) ? Math.max(0, delay) : 0;
}
