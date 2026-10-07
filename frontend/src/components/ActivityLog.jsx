import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import Spinner from './Spinner';

const PAGE = 50;
const statusStyle = {
  success: 'bg-brand-100 text-brand-700',
  pending: 'bg-amber-100 text-amber-700',
  denied: 'bg-red-100 text-red-700',
  error: 'bg-red-100 text-red-700',
};

/** Who did what to this database (owners only). */
export default function ActivityLog({ databaseId }) {
  const [entries, setEntries] = useState([]);
  const [writesOnly, setWritesOnly] = useState(true);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(
    async (before) => {
      setLoading(true);
      setError('');
      try {
        const response = await api.get(`/databases/${databaseId}/activity`, {
          params: { limit: PAGE, writes_only: writesOnly, ...(before ? { before } : {}) },
        });
        setEntries((prev) => (before ? [...prev, ...response.data] : response.data));
        setDone(response.data.length < PAGE);
      } catch (err) {
        setError(getErrorMessage(err, 'Could not load activity.'));
      } finally {
        setLoading(false);
      }
    },
    [databaseId, writesOnly]
  );

  useEffect(() => {
    load(null);
  }, [load]);

  return (
    <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Activity</h2>
        <label className="flex items-center gap-2 text-sm text-gray-600">
          <input type="checkbox" checked={writesOnly} onChange={(e) => setWritesOnly(e.target.checked)} />
          Changes only
        </label>
      </div>
      {error && <p className="text-sm text-red-600">{error}</p>}
      {entries.length === 0 && !loading ? (
        <p className="text-sm text-gray-400">Nothing yet.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {entries.map((e) => (
            <li key={e.id} className="py-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${statusStyle[e.status] || 'bg-gray-100'}`}>
                  {e.status}
                </span>
                <span className="font-medium text-gray-800">{e.tool_name}</span>
                <span className="text-gray-500">by {e.user_email || 'a deleted user'}</span>
                <span className="text-gray-400 ml-auto">{new Date(e.timestamp).toLocaleString()}</span>
              </div>
              {(e.sql || e.result?.undone_sql) && (
                <code className="block mt-1 text-xs text-gray-600 truncate" title={e.sql || e.result.undone_sql}>
                  {e.sql || `undid: ${e.result.undone_sql}`}
                </code>
              )}
            </li>
          ))}
        </ul>
      )}
      {!done && entries.length > 0 && (
        <button
          onClick={() => load(entries[entries.length - 1].timestamp)}
          disabled={loading}
          className="text-sm font-medium text-brand-700 hover:text-brand-800 disabled:opacity-50"
        >
          Load older
        </button>
      )}
      {loading && <Spinner className="h-4 w-4 text-gray-400" />}
    </div>
  );
}
