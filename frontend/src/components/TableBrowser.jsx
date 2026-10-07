import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import Spinner from './Spinner';

const PAGE_SIZE = 50;

/** A table's columns and a paginated, sortable view of its rows. */
export default function TableBrowser({ databaseId, table, refreshKey = 0 }) {
  const [columns, setColumns] = useState([]);
  const [page, setPage] = useState(null);
  const [offset, setOffset] = useState(0);
  const [sort, setSort] = useState({ column: null, descending: false });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    setOffset(0);
    setSort({ column: null, descending: false });
    api
      .get(`/databases/${databaseId}/tables/${table}`)
      .then((response) => setColumns(response.data.columns))
      .catch((err) => setError(getErrorMessage(err, 'Could not load the table.')));
  }, [databaseId, table, refreshKey]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await api.get(`/databases/${databaseId}/tables/${table}/rows`, {
        params: {
          offset,
          limit: PAGE_SIZE,
          ...(sort.column ? { order_by: sort.column, descending: sort.descending } : {}),
        },
      });
      setPage(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load rows.'));
    } finally {
      setLoading(false);
    }
  }, [databaseId, table, offset, sort]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const toggleSort = (column) => {
    setOffset(0);
    setSort((s) => (s.column === column ? { column, descending: !s.descending } : { column, descending: false }));
  };

  const total = page?.total ?? 0;
  const last = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-5 space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-gray-900">{table}</h2>
        <span className="text-sm text-gray-400">{total.toLocaleString()} rows</span>
      </div>

      <div className="flex flex-wrap gap-2">
        {columns.map((c) => (
          <span key={c.name} className="text-xs bg-gray-50 border border-gray-200 rounded-lg px-2 py-1 text-gray-600">
            {c.primary_key && <span className="text-amber-600 font-semibold mr-1" title="Primary key">PK</span>}
            <span className="font-medium text-gray-800">{c.name}</span> {c.type}
            {!c.nullable && <span className="text-gray-400"> · not null</span>}
          </span>
        ))}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="overflow-auto max-h-[32rem] border border-gray-200 rounded-lg">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-brand-50">
            <tr>
              {(page?.columns || []).map((col) => (
                <th key={col} className="text-left font-medium text-gray-700 px-3 py-2 border-b border-gray-200 whitespace-nowrap">
                  <button onClick={() => toggleSort(col)} className="hover:text-brand-700">
                    {col}
                    {sort.column === col ? (sort.descending ? ' ↓' : ' ↑') : ''}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {page && page.rows.length === 0 && (
              <tr>
                <td colSpan={Math.max(page.columns.length, 1)} className="px-3 py-6 text-center text-gray-400 italic">
                  This table is empty.
                </td>
              </tr>
            )}
            {(page?.rows || []).map((row, i) => (
              <tr key={i} className="hover:bg-brand-50/40">
                {row.map((cell, j) => (
                  <td key={j} className="px-3 py-2 text-gray-700 whitespace-nowrap">
                    {cell === null ? <span className="text-gray-300 italic">null</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm">
        <span className="text-gray-500 flex items-center gap-2">
          {loading && <Spinner className="h-3 w-3" />}
          {total > 0 ? `${offset + 1}–${last} of ${total.toLocaleString()}` : ''}
        </span>
        <div className="flex gap-2">
          <button
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            disabled={offset === 0 || loading}
            className="px-3 py-1.5 rounded-lg border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
          >
            Previous
          </button>
          <button
            onClick={() => setOffset(offset + PAGE_SIZE)}
            disabled={last >= total || loading}
            className="px-3 py-1.5 rounded-lg border border-gray-200 disabled:opacity-40 hover:bg-gray-50"
          >
            Next
          </button>
        </div>
      </div>
    </div>
  );
}
