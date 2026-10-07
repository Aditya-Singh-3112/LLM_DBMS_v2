import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import Spinner from './Spinner';
import { formatWhen } from '../format';

/** The latest confirmed change to the database, with an Undo button. */
export default function UndoBar({ databaseId, refreshKey = 0, canWrite, onUndone, onError }) {
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const response = await api.get(`/databases/${databaseId}/undo`);
      setStatus(response.data);
    } catch {
      setStatus(null);
    }
  }, [databaseId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (!status?.sql) return null;

  const undo = async () => {
    if (!window.confirm(`Undo this change?\n\n${status.sql}`)) return;
    setBusy(true);
    try {
      await api.post(`/databases/${databaseId}/undo`);
      setStatus(null);
      onUndone?.();
    } catch (err) {
      onError?.(getErrorMessage(err, 'Undo failed.'));
      load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-3 bg-white border border-gray-200 rounded-xl px-4 py-2.5 text-sm">
      <div className="min-w-0">
        <span className="text-gray-500">
          Last change{status.by_you ? ' (yours)' : ''}, {formatWhen(status.created_at)}:{' '}
        </span>
        <code className="text-gray-800 truncate">{status.sql}</code>
        {!status.available && status.unavailable_reason && (
          <span className="text-gray-400"> · can't be undone: {status.unavailable_reason}</span>
        )}
      </div>
      {status.available && canWrite && (
        <button
          onClick={undo}
          disabled={busy}
          className="flex items-center gap-1.5 flex-shrink-0 text-sm font-medium text-brand-700 hover:text-brand-800 disabled:opacity-50"
        >
          {busy && <Spinner className="h-3 w-3" />} Undo
        </button>
      )}
    </div>
  );
}
