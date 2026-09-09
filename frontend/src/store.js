import create from 'zustand';

const useAuthStore = create((set) => ({
  accessToken: localStorage.getItem('accessToken'),
  refreshToken: localStorage.getItem('refreshToken'),
  user: null,
  isAuthenticated: !!localStorage.getItem('accessToken'),

  setTokens: (accessToken, refreshToken) => {
    localStorage.setItem('accessToken', accessToken);
    localStorage.setItem('refreshToken', refreshToken);
    set({ accessToken, refreshToken, isAuthenticated: true });
  },

  setUser: (user) => set({ user }),

  logout: () => {
    localStorage.removeItem('accessToken');
    localStorage.removeItem('refreshToken');
    set({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
    });
  },

  refreshAccessToken: async () => {
    const refreshToken = localStorage.getItem('refreshToken');
    if (!refreshToken) return false;

    try {
      const response = await fetch('/auth/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      });

      if (response.ok) {
        const data = await response.json();
        localStorage.setItem('accessToken', data.access_token);
        localStorage.setItem('refreshToken', data.refresh_token);
        set({ accessToken: data.access_token, refreshToken: data.refresh_token });
        return true;
      }
    } catch (error) {
      console.error('Token refresh failed:', error);
    }

    return false;
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

const HISTORY_LIMIT = 20;

const useQueryHistoryStore = create((set, get) => ({
  historyByDb: JSON.parse(localStorage.getItem('queryHistory') || '{}'),

  addQuery: (databaseId, query) => {
    const historyByDb = { ...get().historyByDb };
    const existing = historyByDb[databaseId] || [];
    const updated = [
      { query, timestamp: Date.now() },
      ...existing.filter((h) => h.query !== query),
    ].slice(0, HISTORY_LIMIT);

    historyByDb[databaseId] = updated;
    localStorage.setItem('queryHistory', JSON.stringify(historyByDb));
    set({ historyByDb });
  },

  getHistory: (databaseId) => get().historyByDb[databaseId] || [],

  clearHistory: (databaseId) => {
    const historyByDb = { ...get().historyByDb };
    delete historyByDb[databaseId];
    localStorage.setItem('queryHistory', JSON.stringify(historyByDb));
    set({ historyByDb });
  },
}));

export { useAuthStore, useDatabaseStore, useQueryHistoryStore };