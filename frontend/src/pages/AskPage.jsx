import React, { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useDatabaseStore } from '../store';
import api from '../api';
import { getErrorMessage } from '../api'; 

export default function AskPage() {
  const { databaseId } = useParams();
  const selectedDatabase = useDatabaseStore(
    (state) => state.selectedDatabase
  );

  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exportFormat, setExportFormat] = useState('csv');

  const handleAsk = async (e) => {
    e.preventDefault();
    if (!query.trim()) return;

    setError('');
    setLoading(true);
    setResult(null);

    try {
      const response = await api.post(`/databases/${databaseId}/ask`, {
        query,
      });
      setResult(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Query failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleExport = async () => {
    if (!result?.sql) {
      setError('No SQL query to export');
      return;
    }

    try {
      const response = await api.post(
        `/databases/${databaseId}/export`,
        {
          sql: result.sql,
          format: exportFormat,
        },
        {
          responseType: 'blob',
        }
      );

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `export.${exportFormat}`);
      document.body.appendChild(link);
      link.click();
      link.parentElement.removeChild(link);
    } catch (err) {
      setError('Export failed');
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          {selectedDatabase?.name || 'Database Query'}
        </h1>

        <form onSubmit={handleAsk} className="bg-white rounded-lg shadow p-6 mb-8">
          <label className="block text-sm font-medium text-gray-700 mb-2">
            Ask a question
          </label>
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g., Show me all customers who made purchases in the last 30 days"
            className="w-full h-24 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          <button
            type="submit"
            disabled={loading}
            className="mt-4 bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-2 rounded-lg disabled:opacity-50"
          >
            {loading ? 'Querying...' : 'Ask'}
          </button>
        </form>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded mb-8">
            {error}
          </div>
        )}

        {result && (
          <div className="bg-white rounded-lg shadow p-6 space-y-6">
            <div>
              <h2 className="text-lg font-bold text-gray-900 mb-2">Answer</h2>
              <p className="text-gray-700">{result.answer}</p>
            </div>

            {result.sql && (
              <div>
                <h2 className="text-lg font-bold text-gray-900 mb-2">SQL</h2>
                <pre className="bg-gray-100 p-4 rounded text-sm text-gray-800 overflow-x-auto">
                  {result.sql}
                </pre>
              </div>
            )}

            {result.tool_calls && result.tool_calls.length > 0 && (
              <div>
                <h2 className="text-lg font-bold text-gray-900 mb-2">
                  Tool Calls ({result.tool_calls.length})
                </h2>
                <div className="space-y-2">
                  {result.tool_calls.map((call, idx) => (
                    <div
                      key={idx}
                      className={`p-3 rounded ${
                        call.source === 'rag'
                          ? 'bg-yellow-50 border border-yellow-200'
                          : 'bg-blue-50 border border-blue-200'
                      }`}
                    >
                      <div className="font-medium text-gray-900">
                        {call.tool_name}
                        <span className="ml-2 text-xs font-normal px-2 py-1 rounded bg-gray-200">
                          {call.source}
                        </span>
                      </div>
                      <div className="text-sm text-gray-600">
                        Duration: {call.duration_ms}ms
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {result.result?.rows && (
              <div>
                <h2 className="text-lg font-bold text-gray-900 mb-2">
                  Results ({result.result.rows.length} rows)
                </h2>

                <div className="flex gap-2 mb-4">
                  <select
                    value={exportFormat}
                    onChange={(e) => setExportFormat(e.target.value)}
                    className="px-3 py-2 border border-gray-300 rounded"
                  >
                    <option value="csv">CSV</option>
                    <option value="xlsx">XLSX</option>
                  </select>
                  <button
                    onClick={handleExport}
                    className="bg-green-600 hover:bg-green-700 text-white font-medium px-4 py-2 rounded"
                  >
                    Export
                  </button>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full border-collapse border border-gray-300">
                    <thead>
                      <tr className="bg-gray-100">
                        {result.result.columns.map((col, idx) => (
                          <th
                            key={idx}
                            className="border border-gray-300 px-4 py-2 text-left font-medium text-gray-900"
                          >
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.result.rows.map((row, rowIdx) => (
                        <tr
                          key={rowIdx}
                          className={
                            rowIdx % 2 === 0
                              ? 'bg-white'
                              : 'bg-gray-50'
                          }
                        >
                          {row.map((cell, cellIdx) => (
                            <td
                              key={cellIdx}
                              className="border border-gray-300 px-4 py-2 text-gray-800"
                            >
                              {String(cell)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {result.grounded_on && result.grounded_on.length > 0 && (
              <div className="bg-purple-50 border border-purple-200 p-4 rounded">
                <h3 className="font-bold text-purple-900 mb-2">
                  Grounded On
                </h3>
                <div className="space-y-1">
                  {result.grounded_on.map((ref, idx) => (
                    <div key={idx} className="text-sm text-purple-800">
                      {ref.source}
                      {ref.chapter && ` - ${ref.chapter}`}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="text-sm text-gray-500 pt-4 border-t">
              Query completed in {result.total_execution_time_ms}ms
            </div>
          </div>
        )}
      </div>
    </div>
  );
}