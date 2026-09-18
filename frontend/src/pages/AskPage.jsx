import React, { useEffect, useRef, useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useDatabaseStore, useQueryHistoryStore } from '../store';
import api, { getErrorMessage } from '../api';
import { streamAsk } from '../streamAsk';
import { downloadResponse } from '../download';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';
import TablesPanel from '../components/TablesPanel';
import TurnCard from '../components/TurnCard';

let nextTurnId = 1;

export default function AskPage() {
  const { databaseId } = useParams();
  const selectedDatabase = useDatabaseStore((state) => state.selectedDatabase);
  const setSelectedDatabase = useDatabaseStore((state) => state.setSelectedDatabase);
  const addQuery = useQueryHistoryStore((state) => state.addQuery);
  const getHistory = useQueryHistoryStore((state) => state.getHistory);
  const clearHistory = useQueryHistoryStore((state) => state.clearHistory);

  const [query, setQuery] = useState('');
  const [turns, setTurns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exportFormat, setExportFormat] = useState('csv');
  const [history, setHistory] = useState([]);
  const [tablesRefreshKey, setTablesRefreshKey] = useState(0);
  const bottomRef = useRef(null);
  const abortRef = useRef(null);

  const canWrite =
    selectedDatabase?.access_level === 'owner' || selectedDatabase?.access_level === 'write';

  useEffect(() => {
    setHistory(getHistory(databaseId));
    setTurns([]);
  }, [databaseId, getHistory]);

  // The store is in-memory only, so after a page reload we no longer know
  // which database this route refers to; look it up.
  useEffect(() => {
    if (selectedDatabase?.id === databaseId) return;
    let cancelled = false;
    api
      .get('/databases')
      .then((response) => {
        if (cancelled) return;
        const match = response.data.find((db) => db.id === databaseId);
        if (match) setSelectedDatabase(match);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [databaseId, selectedDatabase, setSelectedDatabase]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  // Abort an in-flight stream when leaving the page.
  useEffect(() => () => abortRef.current?.abort(), []);

  const updateTurn = (id, patch) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === 'function' ? patch(t) : patch) } : t)));

  const runQuery = async (q, { reset = false } = {}) => {
    const question = q.trim();
    if (!question || loading) return;

    const id = nextTurnId++;
    const started = Date.now();
    setError('');
    setLoading(true);
    setQuery('');
    setTurns((prev) => [
      ...(reset ? [] : prev),
      { id, question, answer: '', toolCalls: [], streaming: true, sql: null, result: null, groundedOn: null, pendingWrite: null, elapsedMs: null, error: null },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamAsk(
        databaseId,
        { query: question, reset },
        (event, data) => {
          if (event === 'token') {
            updateTurn(id, (t) => ({ answer: t.answer + data.text }));
          } else if (event === 'tool_end') {
            updateTurn(id, (t) => ({ toolCalls: [...t.toolCalls, data] }));
          } else if (event === 'done') {
            updateTurn(id, {
              streaming: false,
              answer: data.answer,
              toolCalls: data.tool_calls,
              sql: data.sql,
              result: data.result,
              groundedOn: data.grounded_on,
              pendingWrite: data.pending_write ? { sql: data.pending_write.sql } : null,
              elapsedMs: data.total_execution_time_ms,
            });
          } else if (event === 'error') {
            updateTurn(id, { streaming: false, error: data.detail, elapsedMs: Date.now() - started });
          }
        },
        { signal: controller.signal }
      );
      addQuery(databaseId, question);
      setHistory(getHistory(databaseId));
      setTablesRefreshKey((k) => k + 1);
    } catch (err) {
      if (err.name !== 'AbortError') {
        updateTurn(id, { streaming: false, error: err.message || 'Query failed.' });
      }
    } finally {
      updateTurn(id, (t) => (t.streaming ? { streaming: false } : {}));
      setLoading(false);
      abortRef.current = null;
    }
  };

  const handleAsk = (e) => {
    e.preventDefault();
    runQuery(query);
  };

  const handleNewConversation = async () => {
    try {
      await api.post(`/databases/${databaseId}/conversation/clear`);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not clear the conversation.'));
      return;
    }
    setTurns([]);
  };

  const handleConfirmWrite = async (turn) => {
    updateTurn(turn.id, (t) => ({ pendingWrite: { ...t.pendingWrite, running: true, error: null } }));
    try {
      const response = await api.post(`/databases/${databaseId}/sql/confirm`, { sql: turn.pendingWrite.sql });
      updateTurn(turn.id, (t) => ({
        pendingWrite: null,
        sql: t.pendingWrite.sql,
        result: response.data,
        answer: `${t.answer}\n\n✓ Executed.`,
      }));
      setTablesRefreshKey((k) => k + 1);
    } catch (err) {
      updateTurn(turn.id, (t) => ({
        pendingWrite: { ...t.pendingWrite, running: false, error: getErrorMessage(err, 'Execution failed.') },
      }));
    }
  };

  const handleDismissWrite = (turn) => {
    updateTurn(turn.id, (t) => ({ pendingWrite: null, answer: `${t.answer}\n\n✕ Cancelled.` }));
  };

  const handleExport = async (turn) => {
    if (!turn.sql) return;
    try {
      const response = await api.post(
        `/databases/${databaseId}/export`,
        { sql: turn.sql, format: exportFormat },
        { responseType: 'blob' }
      );
      downloadResponse(response, `export.${exportFormat}`);
    } catch (err) {
      setError(getErrorMessage(err, 'Export failed.'));
    }
  };

  return (
    <Layout>
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <Link to="/databases" className="text-sm text-brand-700 hover:underline">
            ← All databases
          </Link>
          <h1 className="text-2xl font-bold text-gray-900 mt-1">
            {selectedDatabase?.name || 'Database'}
          </h1>
        </div>
        {turns.length > 0 && (
          <button
            onClick={handleNewConversation}
            disabled={loading}
            className="text-sm font-medium text-gray-500 hover:text-brand-700 disabled:opacity-50"
          >
            New conversation
          </button>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Sidebar: tables + history */}
        <aside className="lg:col-span-1 order-2 lg:order-1 space-y-6 lg:sticky lg:top-20 self-start">
          <TablesPanel
            databaseId={databaseId}
            refreshKey={tablesRefreshKey}
            onError={setError}
            canWrite={canWrite}
          />
          <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4">
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
                      onClick={() => runQuery(h.query)}
                      disabled={loading}
                      className="w-full text-left text-sm text-gray-600 hover:bg-brand-50 hover:text-brand-700 rounded-lg px-2 py-1.5 truncate transition disabled:opacity-50"
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
          {error && (
            <Banner type="error" onDismiss={() => setError('')}>
              {error}
            </Banner>
          )}

          {turns.length === 0 ? (
            <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-8 text-center text-gray-400 text-sm">
              Ask anything about this database. Follow-up questions build on the previous answer.
            </div>
          ) : (
            <div className="space-y-6">
              {turns.map((turn) => (
                <TurnCard
                  key={turn.id}
                  turn={turn}
                  exportFormat={exportFormat}
                  onExportFormatChange={setExportFormat}
                  onExport={handleExport}
                  onConfirmWrite={handleConfirmWrite}
                  onDismissWrite={handleDismissWrite}
                />
              ))}
              <div ref={bottomRef} />
            </div>
          )}

          <form onSubmit={handleAsk} className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4 sticky bottom-4">
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  runQuery(query);
                }
              }}
              placeholder={turns.length ? 'Ask a follow-up…' : 'e.g., Show me all customers who made purchases in the last 30 days'}
              className="w-full h-20 px-4 py-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-brand-500 focus:border-transparent outline-none transition resize-none text-sm"
            />
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-gray-400">Enter to send · Shift+Enter for a new line</span>
              <button
                type="submit"
                disabled={loading || !query.trim()}
                className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white font-medium px-5 py-2 rounded-xl transition disabled:opacity-50 text-sm"
              >
                {loading && <Spinner className="h-4 w-4" />}
                {loading ? 'Thinking...' : 'Ask'}
              </button>
            </div>
          </form>
        </div>
      </div>
    </Layout>
  );
}
