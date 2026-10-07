import { create } from 'zustand';
import { API_BASE } from './apiBase';

const useAuthStore = create((set, get) => ({
  // The access token lives only in memory; the refresh token is an HttpOnly
  // cookie the browser sends to /auth/* on its own.
  accessToken: null,
  user: null,
  isAuthenticated: false,
  // true until the first refresh attempt on page load has completed
  bootstrapping: true,

  setAccessToken: (accessToken) => set({ accessToken, isAuthenticated: !!accessToken }),

  setUser: (user) => set({ user }),

  // Re-read the signed-in user (e.g. after verifying the email address).
  refreshUser: async () => {
    const token = get().accessToken;
    if (!token) return;
    try {
      const response = await fetch(`${API_BASE}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
      if (response.ok) set({ user: await response.json() });
    } catch {
      // non-fatal
    }
  },

  logout: async () => {
    try {
      await fetch(`${API_BASE}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });
    } catch {
      // the local session is cleared regardless
    }
    set({ accessToken: null, user: null, isAuthenticated: false });
  },

  refreshAccessToken: async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
      });

      if (response.ok) {
        const data = await response.json();
        set({ accessToken: data.access_token, isAuthenticated: true });
        return true;
      }
    } catch (error) {
      console.error('Token refresh failed:', error);
    }

    set({ accessToken: null, isAuthenticated: false });
    return false;
  },

  // Called once on app start: restore the session from the cookie, if any.
  bootstrap: async () => {
    if (!get().bootstrapping) return;
    const ok = await get().refreshAccessToken();
    if (ok) {
      try {
        const response = await fetch(`${API_BASE}/auth/me`, {
          headers: { Authorization: `Bearer ${get().accessToken}` },
        });
        if (response.ok) set({ user: await response.json() });
      } catch {
        // non-fatal
      }
    }
    set({ bootstrapping: false });
  },
}));

const useDatabaseStore = create((set) => ({
  databases: [],
  selectedDatabase: null,

  setDatabases: (databases) => set({ databases }),
  setSelectedDatabase: (database) => set({ selectedDatabase: database }),

  addDatabase: (database) =>
    set((state) => ({ databases: [...state.databases, database] })),

  removeDatabase: (databaseId) =>
    set((state) => ({
      databases: state.databases.filter((db) => db.id !== databaseId),
    })),
}));

export { useAuthStore, useDatabaseStore };
