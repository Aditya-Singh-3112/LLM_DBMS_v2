import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import { downloadResponse } from '../download';
import Spinner from './Spinner';
import ImportModal from './ImportModal';
import StorageMeter from './StorageMeter';

export default function TablesPanel({ databaseId, refreshKey = 0, onError, canWrite = false, selected, onSelect, onImported }) {
  const [showImport, setShowImport] = useState(false);
  const [tables, setTables] = useState([]);
  const [loading, setLoading] = useState(true);
  const [format, setFormat] = useState('csv');
  const [downloading, setDownloading] = useState(null);

  const fetchTables = useCallback(async () => {
    setLoading(true);
    try {
      const response = await api.get(`/databases/${databaseId}/tables`);
      setTables(response.data.tables || []);
    } catch (err) {
      onError?.(getErrorMessage(err, 'Failed to load tables.'));
    } finally {
      setLoading(false);
    }
  }, [databaseId, onError]);

  // refreshKey lets the parent re-list after a query that may have created a table.
  useEffect(() => {
    fetchTables();
  }, [fetchTables, refreshKey]);

  const handleDownload = async (table) => {
    setDownloading(table);
    try {
      const response = await api.get(
        `/databases/${databaseId}/tables/${table}/export`,
        { params: { format }, responseType: 'blob' }
      );
      downloadResponse(response, `${table}.${format}`);
    } catch (err) {
      // Blob error bodies need decoding before getErrorMessage can read them.
      let message = 'Download failed.';
      try {
        const text = await err.response?.data?.text?.();
        const detail = text && JSON.parse(text)?.detail;
        if (typeof detail === 'string') message = detail;
      } catch {
        // keep fallback
      }
      onError?.(message);
    } finally {
      setDownloading(null);
    }
  };

  return (
    <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-900">Tables</h2>
        <div className="flex items-center gap-2">
        {canWrite && (
          <button
            onClick={() => setShowImport(true)}
            className="text-xs font-medium text-brand-700 hover:text-brand-800"
          >
            + Import
          </button>
        )}
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value)}
          className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white"
          aria-label="Download format"
        >
          <option value="csv">CSV</option>
          <option value="xlsx">XLSX</option>
        </select>
        </div>
      </div>

      {showImport && (
        <ImportModal
          databaseId={databaseId}
          onClose={() => setShowImport(false)}
          onImported={() => {
            fetchTables();
            onImported?.();
          }}
        />
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <Spinner className="h-3 w-3" /> Loading tables...
        </div>
      ) : tables.length === 0 ? (
        <p className="text-xs text-gray-400">No tables yet. Ask the assistant to create one.</p>
      ) : (
        <ul className="space-y-1 max-h-64 overflow-y-auto">
          {tables.map((table) => (
            <li key={table} className="flex items-center justify-between gap-2">
              {onSelect ? (
                <button
                  onClick={() => onSelect(table)}
                  title={table}
                  className={`flex-1 min-w-0 text-left text-sm truncate rounded-lg px-2 py-1 ${
                    selected === table ? 'bg-brand-50 text-brand-800 font-medium' : 'text-gray-700 hover:bg-brand-50'
                  }`}
                >
                  {table}
                </button>
              ) : (
                <span className="text-sm text-gray-700 truncate" title={table}>
                  {table}
                </span>
              )}
              <button
                onClick={() => handleDownload(table)}
                disabled={downloading !== null}
                className="flex items-center gap-1 text-xs font-medium text-brand-700 hover:text-brand-800 disabled:opacity-50 flex-shrink-0"
                title={`Download ${table} as ${format.toUpperCase()}`}
              >
                {downloading === table ? <Spinner className="h-3 w-3" /> : (
                  <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M10 2a1 1 0 011 1v8.586l2.293-2.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 111.414-1.414L9 11.586V3a1 1 0 011-1z" />
                    <path d="M3 15a1 1 0 011 1v1h12v-1a1 1 0 112 0v2a1 1 0 01-1 1H3a1 1 0 01-1-1v-2a1 1 0 011-1z" />
                  </svg>
                )}
                Download
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 pt-3 border-t border-gray-100">
        <StorageMeter databaseId={databaseId} refreshKey={refreshKey} />
      </div>
    </div>
  );
}
