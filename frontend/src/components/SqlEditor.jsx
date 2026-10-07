import React, { useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import { downloadResponse } from '../download';
import { pluralize } from '../format';
import ResultsTable from './ResultsTable';
import Spinner from './Spinner';
import WriteConfirm from './WriteConfirm';

/**
 * Write and run SQL directly. SELECTs run at once; writes come back as a
 * dry run to confirm (POST /sql answers 409 confirmation_required).
 */
export default function SqlEditor({ databaseId, sql, onSqlChange, onSave, onWritten }) {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [pending, setPending] = useState(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [exportFormat, setExportFormat] = useState('csv');

  useEffect(() => {
    setPending(null);
  }, [sql]);

  const run = async () => {
    if (!sql.trim() || running) return;
    setRunning(true);
    setError('');
    setNotice('');
    setPending(null);
    try {
      const response = await api.post(`/databases/${databaseId}/sql`, { sql });
      setResult(response.data);
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (err?.response?.status === 409 && detail?.code === 'confirmation_required') {
        setPending(detail);
        setResult(null);
      } else {
        setError(getErrorMessage(err, 'The query failed.'));
      }
    } finally {
      setRunning(false);
    }
  };

  const confirm = async () => {
    setPending((p) => ({ ...p, running: true, error: null }));
    try {
      const response = await api.post(`/databases/${databaseId}/sql/confirm`, { sql: pending.sql });
      const data = response.data;
      setPending(null);
      setResult(data.columns.length ? { ...data, truncated: false } : null);
      setNotice(
        `Done${data.rows_affected != null ? `: ${pluralize(data.rows_affected, 'row')} affected` : ''}.` +
          (data.undo_available ? ' You can undo it from the bar above.' : '')
      );
      onWritten?.();
    } catch (err) {
      setPending((p) => ({ ...p, running: false, error: getErrorMessage(err, 'Execution failed.') }));
    }
  };

  const exportResult = async () => {
    try {
      const response = await api.post(
        `/databases/${databaseId}/export`,
        { sql, format: exportFormat },
        { responseType: 'blob' }
      );
      downloadResponse(response, `export.${exportFormat}`);
    } catch {
      setError('Export failed.');
    }
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4">
        <textarea
          value={sql}
          onChange={(e) => onSqlChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              run();
            }
          }}
          spellCheck={false}
          aria-label="SQL"
          placeholder="SELECT * FROM customers WHERE city = 'Pune'"
          className="w-full h-40 px-4 py-3 font-mono text-sm border border-gray-200 rounded-xl focus:ring-2 focus:ring-brand-500 focus:border-transparent outline-none resize-y"
        />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-xs text-gray-400">Ctrl+Enter to run · writes ask for confirmation first</span>
          <div className="flex gap-2">
            <button
              onClick={() => onSave(sql)}
              disabled={!sql.trim()}
              className="text-sm font-medium text-gray-600 hover:text-brand-700 px-3 py-2 disabled:opacity-50"
            >
              Save
            </button>
            <button
              onClick={run}
              disabled={running || !sql.trim()}
              className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white font-medium px-5 py-2 rounded-xl transition disabled:opacity-50 text-sm"
            >
              {running && <Spinner className="h-4 w-4" />} Run
            </button>
          </div>
        </div>
      </div>

      {error && <p className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</p>}
      {notice && <p className="text-sm text-brand-800 bg-brand-50 border border-brand-200 rounded-lg px-4 py-3">{notice}</p>}
      {pending && <WriteConfirm pending={pending} onConfirm={confirm} onCancel={() => setPending(null)} />}

      {result && (
        <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-5 space-y-2">
          {result.truncated && (
            <p className="text-xs text-amber-700">Showing the first 1,000 rows. Export to get all of them.</p>
          )}
          <ResultsTable
            columns={result.columns}
            rows={result.rows}
            exportFormat={exportFormat}
            onExportFormatChange={setExportFormat}
            onExport={exportResult}
          />
        </div>
      )}
    </div>
  );
}
