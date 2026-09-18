import React, { useState } from 'react';
import api, { getErrorMessage } from '../api';
import Banner from './Banner';
import Spinner from './Spinner';

const MODES = [
  { value: 'create', label: 'Create new table' },
  { value: 'append', label: 'Append to existing table' },
  { value: 'replace', label: 'Replace existing table' },
];

export default function ImportModal({ databaseId, onClose, onImported }) {
  const [file, setFile] = useState(null);
  const [tableName, setTableName] = useState('');
  const [mode, setMode] = useState('create');
  const [plan, setPlan] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const buildForm = (dryRun) => {
    const form = new FormData();
    form.append('file', file);
    if (tableName.trim()) form.append('table_name', tableName.trim());
    form.append('mode', mode);
    form.append('dry_run', dryRun ? 'true' : 'false');
    return form;
  };

  const handleFile = (e) => {
    const f = e.target.files?.[0] || null;
    setFile(f);
    setPlan(null);
    setError('');
    if (f && !tableName) {
      setTableName(
        f.name
          .replace(/\.[^.]+$/, '')
          .toLowerCase()
          .replace(/[^a-z0-9_]+/g, '_')
          .replace(/^_+|_+$/g, '')
          .replace(/^(\d)/, 'c_$1')
      );
    }
  };

  const preview = async () => {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      const response = await api.post(`/databases/${databaseId}/tables/import`, buildForm(true));
      setPlan(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not read the file.'));
    } finally {
      setBusy(false);
    }
  };

  const runImport = async () => {
    setBusy(true);
    setError('');
    try {
      const response = await api.post(`/databases/${databaseId}/tables/import`, buildForm(false));
      onImported?.(response.data);
      onClose();
    } catch (err) {
      setError(getErrorMessage(err, 'Import failed.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-gray-900/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-lg border border-gray-100 w-full max-w-2xl max-h-[90vh] overflow-y-auto p-6 space-y-4"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">Import a table</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        {error && <Banner type="error" onDismiss={() => setError('')}>{error}</Banner>}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">File (.csv or .xlsx)</label>
          <input type="file" accept=".csv,.txt,.xlsx,.xlsm" onChange={handleFile} className="text-sm" />
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Table name</label>
            <input
              type="text"
              value={tableName}
              onChange={(e) => { setTableName(e.target.value); setPlan(null); }}
              pattern="[A-Za-z_][A-Za-z0-9_]{0,62}"
              className="w-full px-3 py-2 border border-gray-200 rounded-xl text-sm focus:ring-2 focus:ring-brand-500 outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Mode</label>
            <select
              value={mode}
              onChange={(e) => { setMode(e.target.value); setPlan(null); }}
              className="w-full px-3 py-2 border border-gray-200 rounded-xl text-sm bg-white"
            >
              {MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
            </select>
          </div>
        </div>

        {plan && (
          <div className="space-y-3">
            <p className="text-sm text-gray-600">
              <strong>{plan.row_count}</strong> rows will be written to <code>{plan.table_name}</code>
              {plan.skipped_rows > 0 && ` (${plan.skipped_rows} empty rows skipped)`}.
            </p>
            <div className="overflow-auto border border-gray-200 rounded-lg max-h-64">
              <table className="w-full text-xs">
                <thead className="bg-brand-50 sticky top-0">
                  <tr>
                    {plan.columns.map((c) => (
                      <th key={c.name} className="text-left px-2 py-1.5 border-b border-gray-200 whitespace-nowrap">
                        <div className="font-medium text-gray-800">{c.name}</div>
                        <div className="text-gray-400 font-normal">{c.type}</div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {plan.preview.map((row, i) => (
                    <tr key={i}>
                      {row.map((cell, j) => (
                        <td key={j} className="px-2 py-1 whitespace-nowrap text-gray-700">
                          {cell === null ? <span className="text-gray-300 italic">null</span> : String(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900">Cancel</button>
          {!plan ? (
            <button
              onClick={preview}
              disabled={!file || busy}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-xl disabled:opacity-50"
            >
              {busy && <Spinner className="h-4 w-4" />} Preview
            </button>
          ) : (
            <button
              onClick={runImport}
              disabled={busy}
              className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white rounded-xl disabled:opacity-50"
            >
              {busy && <Spinner className="h-4 w-4" />} Import {plan.row_count} rows
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
