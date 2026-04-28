/**
 * api.js — Central axios instance with auth interceptors.
 *
 * - Attaches `Authorization: Bearer <token>` from localStorage on every call.
 * - On 401, clears the stored token and redirects to /login (handled globally
 *   so individual components don't have to worry about session expiry).
 */
import axios from 'axios';

// Allow the API base to be injected at build time via Vite env. Falls back to
// localhost:8000 for the old standalone dev workflow.
export const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';
export const TOKEN_KEY = 'iasw_token';
export const USER_KEY = 'iasw_user';

const api = axios.create({ baseURL: API_BASE });

// ── Request: attach bearer token if present ──────────────────────────────────
api.interceptors.request.use((config) => {
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) {
    config.headers = config.headers || {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ── Response: clear session + redirect to login on 401 ──────────────────────
api.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status;
    if (status === 401) {
      // Session expired or invalid. Wipe and bounce to login.
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(err);
  }
);

export default api;
