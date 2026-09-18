import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import Banner from './Banner';
import Spinner from './Spinner';

export default function ShareModal({ database, onClose }) {
  const [grants, setGrants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState('');
  const [level, setLevel] = useState('read');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const response = await api.get(`/databases/${database.id}/permissions`);
      setGrants(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load permissions.'));
    } finally {
      setLoading(false);
    }
  }, [database.id]);

  useEffect(() => {
    load();
  }, [load]);

  const grant = async (e) => {
    e.preventDefault();
    if (!email.trim()) return;
    setBusy(true);
    setError('');
    try {
      await api.post(`/databases/${database.id}/share`, { email: email.trim(), access_level: level });
      setEmail('');
      await load();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not share.'));
    } finally {
      setBusy(false);
    }
  };

  const changeLevel = async (g, newLevel) => {
    setError('');
    try {
      await api.post(`/databases/${database.id}/share`, { user_id: g.user_id, access_level: newLevel });
      await load();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not update access.'));
    }
  };

  const revoke = async (g) => {
    setError('');
    try {
      await api.delete(`/databases/${database.id}/permissions/${g.user_id}`);
      await load();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not revoke access.'));
    }
  };

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-gray-900/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-lg border border-gray-100 w-full max-w-lg p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Share “{database.name}”</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        {error && <Banner type="error" onDismiss={() => setError('')}>{error}</Banner>}

        <form onSubmit={grant} className="flex gap-2">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="colleague@example.com"
            required
            className="flex-1 px-3 py-2 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-brand-500 outline-none"
          />
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="px-2 py-2 border border-gray-200 rounded-xl text-sm bg-white"
          >
            <option value="read">Read</option>
            <option value="write">Write</option>
          </select>
          <button
            type="submit"
            disabled={busy}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-xl disabled:opacity-50"
          >
            {busy && <Spinner className="h-4 w-4" />} Share
          </button>
        </form>

        <div>
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">People with access</h3>
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-gray-400"><Spinner className="h-3 w-3" /> Loading…</div>
          ) : grants.length === 0 ? (
            <p className="text-sm text-gray-400">Only you.</p>
          ) : (
            <ul className="divide-y divide-gray-100 border border-gray-100 rounded-xl">
              {grants.map((g) => (
                <li key={g.user_id} className="flex items-center justify-between gap-3 px-3 py-2">
                  <span className="text-sm text-gray-700 truncate">{g.email || g.user_id}</span>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <select
                      value={g.access_level}
                      onChange={(e) => changeLevel(g, e.target.value)}
                      className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white"
                    >
                      <option value="read">Read</option>
                      <option value="write">Write</option>
                    </select>
                    <button onClick={() => revoke(g)} className="text-xs text-red-500 hover:text-red-700">
                      Remove
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
