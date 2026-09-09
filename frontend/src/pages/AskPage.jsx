import React, { useEffect, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useDatabaseStore, useQueryHistoryStore } from '../store';
import api, { getErrorMessage } from '../api';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';
import ReasoningSteps from '../components/ReasoningSteps';
import ResultsTable from '../components/ResultsTable';

export default function AskPage() {
  const { databaseId } = useParams();
  const selectedDatabase = useDatabaseStore((state) => state.selectedDatabase);
  const addQuery = useQueryHistoryStore((state) => state.addQuery);
  const getHistory = useQueryHistoryStore((state) => state.getHistory);
  const clearHistory = useQueryHistoryStore((state) => state.clearHistory);

  const [query, setQuery] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exportFormat, setExportFormat] = useState('csv');
  const [copied, setCopied] = useState(false);
  const [history, setHistory] = useState([]);

  useEffect(() => {
    setHistory(getHistory(databaseId));
  }, [databaseId, getHistory]);

  const runQuery = async (q) => {
    if (!q.trim()) return;

    setError('');
    setLoading(true);
    setResult(null);
    setCopied(false);

    try {
      const response = await api.post(`/databases/${databaseId}/ask`, { query: q });
      setResult(response.data);
      addQuery(databaseId, q);
      setHistory(getHistory(databaseId));
    } catch (err) {
      setError(getErrorMessage(err, 'Query failed. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  const handleAsk = (e) => {
    e.preventDefault();
    runQuery(query);
  };

  const handleHistoryClick = (q) => {
    setQuery(q);
    runQuery(q);
  };

  const handleCopySql = async () => {
    if (!result?.sql) return;
    try {
      await navigator.clipboard.writeText(result.sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard API unavailable — ignore
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
        { sql: result.sql, format: exportFormat },
        { responseType: 'blob' }
      );

      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `export.${exportFormat}`);
      document.body.appendChild(link);
      link.click();
      link.parentElement.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(getErrorMessage(err, 'Export failed.'));
    }
  };

  return (
    <Layout>
      <div className="mb-6">
        <Link to="/databases" className="text-sm text-brand-700 hover:underline">
          ← All databases
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-1">
          {selectedDatabase?.name || 'Database'}
        </h1>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* History sidebar */}
        <aside className="lg:col-span-1 order-2 lg:order-1">
          <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4 sticky top-20">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-gray-900">History</h2>
              {history.length > 0 && (
                <button
                  onClick={() => {
                    clearHistory(databaseId);
                    setHistory([]);
                  }}
                  className="text-xs text-gray-400 hover:text-red-500"
                >
                  Clear
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <p className="text-xs text-gray-400">Your past questions will show up here.</p>
            ) : (
              <ul className="space-y-1 max-h-96 overflow-y-auto">
                {history.map((h, idx) => (
                  <li key={idx}>
                    <button
                      onClick={() => handleHistoryClick(h.query)}
                      className="w-full text-left text-sm text-gray-600 hover:bg-brand-50 hover:text-brand-700 rounded-lg px-2 py-1.5 truncate transition"
                      title={h.query}
                    >
                      {h.query}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>

        {/* Main panel */}
        <div className="lg:col-span-3 order-1 lg:order-2 space-y-6">
          <form onSubmit={handleAsk} className="bg-white rounded-2xl shadow-soft border border-gray-100 p-6">
            <label className="block text-sm font-medium text-gray-700 mb-2">Ask a question</label>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g., Show me all customers who made purchases in the last 30 days"
              className="w-full h-24 px-4 py-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-brand-500 focus:border-transparent outline-none transition resize-none"
            />
            <button
              type="submit"
              disabled={loading}
              className="mt-4 flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white font-medium px-6 py-2.5 rounded-xl transition disabled:opacity-50"
            >
              {loading && <Spinner className="h-4 w-4" />}
              {loading ? 'Thinking...' : 'Ask'}
            </button>
          </form>

          {error && (
            <Banner type="error" onDismiss={() => setError('')}>
              {error}
            </Banner>
          )}

          {result && (
            <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-6 space-y-6">
              <div>
                <h2 className="text-sm font-semibold text-gray-900 mb-2">Answer</h2>
                <p className="text-gray-700 whitespace-pre-wrap">{result.answer}</p>
              </div>

              {result.sql && (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h2 className="text-sm font-semibold text-gray-900">SQL</h2>
                    <button
                      onClick={handleCopySql}
                      className="text-xs font-medium text-brand-700 hover:text-brand-800"
                    >
                      {copied ? 'Copied!' : 'Copy'}
                    </button>
                  </div>
                  <pre className="bg-gray-900 text-brand-100 p-4 rounded-xl text-sm overflow-x-auto">
                    <code>{result.sql}</code>
                  </pre>
                </div>
              )}

              <ReasoningSteps toolCalls={result.tool_calls} />

              {result.result?.rows && (
                <ResultsTable
                  columns={result.result.columns}
                  rows={result.result.rows}
                  exportFormat={exportFormat}
                  onExportFormatChange={setExportFormat}
                  onExport={handleExport}
                />
              )}

              {result.grounded_on && result.grounded_on.length > 0 && (
                <div className="bg-brand-50 border border-brand-100 p-4 rounded-xl">
                  <h3 className="text-sm font-semibold text-brand-800 mb-2">Grounded on</h3>
                  <div className="space-y-1">
                    {result.grounded_on.map((ref, idx) => (
                      <div key={idx} className="text-sm text-brand-700">
                        {ref.source}
                        {ref.chapter && ` — ${ref.chapter}`}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="text-xs text-gray-400 pt-4 border-t border-gray-100">
                Completed in {result.total_execution_time_ms}ms
              </div>
            </div>
          )}
        </div>
      </div>
    </Layout>
  );
}