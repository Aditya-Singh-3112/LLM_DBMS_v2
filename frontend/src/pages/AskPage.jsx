import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import { streamAsk } from '../streamAsk';
import { downloadResponse } from '../download';
import { pluralize } from '../format';
import { useDatabase } from '../hooks/useDatabase';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';
import TablesPanel from '../components/TablesPanel';
import TurnCard from '../components/TurnCard';
import ConversationsPanel from '../components/ConversationsPanel';
import DatabaseHeader from '../components/DatabaseHeader';
import UndoBar from '../components/UndoBar';

let nextTurnId = 1;

const emptyTurn = (question) => ({
  id: nextTurnId++,
  question,
  answer: '',
  toolCalls: [],
  streaming: false,
  sql: null,
  result: null,
  groundedOn: null,
  pendingWrite: null,
  elapsedMs: null,
  error: null,
});

/** Turn a stored conversation (alternating human / ai messages) into turns. */
export function turnsFromMessages(messages) {
  const turns = [];
  for (const message of messages) {
    if (message.role === 'human') {
      turns.push(emptyTurn(message.content));
    } else if (turns.length) {
      Object.assign(turns[turns.length - 1], { answer: message.content, sql: message.sql || null });
    }
  }
  return turns;
}

export default function AskPage() {
  const { databaseId } = useParams();
  const { database, canWrite } = useDatabase(databaseId);

  const [query, setQuery] = useState('');
  const [turns, setTurns] = useState([]);
  const [conversationId, setConversationId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [exportFormat, setExportFormat] = useState('csv');
  const [refreshKey, setRefreshKey] = useState(0);
  const [conversationsKey, setConversationsKey] = useState(0);
  const bottomRef = useRef(null);
  const abortRef = useRef(null);

  const refresh = () => setRefreshKey((k) => k + 1);

  useEffect(() => {
    setTurns([]);
    setConversationId(null);
  }, [databaseId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [turns]);

  // Abort an in-flight stream when leaving the page.
  useEffect(() => () => abortRef.current?.abort(), []);

  const updateTurn = (id, patch) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...(typeof patch === 'function' ? patch(t) : patch) } : t)));

  const runQuery = async (q) => {
    const question = q.trim();
    if (!question || loading) return;

    const turn = { ...emptyTurn(question), streaming: true };
    const started = Date.now();
    setError('');
    setLoading(true);
    setQuery('');
    setTurns((prev) => [...prev, turn]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamAsk(
        databaseId,
        { query: question, conversation_id: conversationId },
        (event, data) => {
          if (event === 'token') {
            updateTurn(turn.id, (t) => ({ answer: t.answer + data.text }));
          } else if (event === 'tool_end') {
            updateTurn(turn.id, (t) => ({ toolCalls: [...t.toolCalls, data] }));
          } else if (event === 'done') {
            setConversationId(data.conversation_id);
            updateTurn(turn.id, {
              streaming: false,
              answer: data.answer,
              toolCalls: data.tool_calls,
              sql: data.sql,
              result: data.result,
              groundedOn: data.grounded_on,
              pendingWrite: data.pending_write,
              elapsedMs: data.total_execution_time_ms,
            });
          } else if (event === 'error') {
            updateTurn(turn.id, { streaming: false, error: data.detail, elapsedMs: Date.now() - started });
          }
        },
        { signal: controller.signal }
      );
      setConversationsKey((k) => k + 1);
      refresh();
    } catch (err) {
      if (err.name !== 'AbortError') {
        updateTurn(turn.id, { streaming: false, error: err.message || 'Query failed.' });
      }
    } finally {
      updateTurn(turn.id, (t) => (t.streaming ? { streaming: false } : {}));
      setLoading(false);
      abortRef.current = null;
    }
  };

  const openConversation = useCallback(
    async (id) => {
      try {
        const response = await api.get(`/databases/${databaseId}/conversations/${id}`);
        setConversationId(id);
        setTurns(turnsFromMessages(response.data.messages));
        setError('');
      } catch (err) {
        setError(getErrorMessage(err, 'Could not open the conversation.'));
      }
    },
    [databaseId]
  );

  const newConversation = () => {
    setConversationId(null);
    setTurns([]);
  };

  const handleConfirmWrite = async (turn) => {
    updateTurn(turn.id, (t) => ({ pendingWrite: { ...t.pendingWrite, running: true, error: null } }));
    try {
      const response = await api.post(`/databases/${databaseId}/sql/confirm`, {
        sql: turn.pendingWrite.sql,
        conversation_id: conversationId,
      });
      const data = response.data;
      const affected = data.rows_affected != null ? ` ${pluralize(data.rows_affected, 'row')} affected.` : '';
      updateTurn(turn.id, (t) => ({
        pendingWrite: null,
        sql: t.pendingWrite.sql,
        result: data.columns.length ? data : null,
        answer: `${t.answer}\n\n✓ Executed.${affected}${data.undo_available ? ' You can undo it from the bar above.' : ''}`,
      }));
      refresh();
    } catch (err) {
      updateTurn(turn.id, (t) => ({
        pendingWrite: { ...t.pendingWrite, running: false, error: getErrorMessage(err, 'Execution failed.') },
      }));
    }
  };

  const handleDismissWrite = (turn) => {
    updateTurn(turn.id, (t) => ({ pendingWrite: null, answer: `${t.answer}\n\n✕ Cancelled.` }));
  };

  const handleShowResults = async (turn) => {
    try {
      const response = await api.post(`/databases/${databaseId}/sql`, { sql: turn.sql });
      updateTurn(turn.id, { result: response.data });
    } catch (err) {
      setError(getErrorMessage(err, 'Could not run that query again.'));
    }
  };

  const handleSaveSql = async (sql) => {
    const name = window.prompt('Name this query');
    if (!name) return;
    try {
      await api.post(`/databases/${databaseId}/saved-queries`, { name, sql });
    } catch (err) {
      setError(getErrorMessage(err, 'Could not save the query.'));
    }
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
      <DatabaseHeader databaseId={databaseId} database={database} />

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        <aside className="lg:col-span-1 order-2 lg:order-1 space-y-6 lg:sticky lg:top-20 self-start">
          <ConversationsPanel
            databaseId={databaseId}
            activeId={conversationId}
            refreshKey={conversationsKey}
            onOpen={openConversation}
            onNew={newConversation}
            onError={setError}
            disabled={loading}
          />
          <TablesPanel databaseId={databaseId} refreshKey={refreshKey} onError={setError} canWrite={canWrite} />
        </aside>

        <div className="lg:col-span-3 order-1 lg:order-2 space-y-6">
          {error && (
            <Banner type="error" onDismiss={() => setError('')}>
              {error}
            </Banner>
          )}
          <UndoBar databaseId={databaseId} refreshKey={refreshKey} canWrite={canWrite} onUndone={refresh} onError={setError} />

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
                  onSaveSql={handleSaveSql}
                  onShowResults={handleShowResults}
                />
              ))}
              <div ref={bottomRef} />
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              runQuery(query);
            }}
            className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4 sticky bottom-4"
          >
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
