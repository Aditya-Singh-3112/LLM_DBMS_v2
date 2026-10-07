import React, { useCallback, useEffect, useState } from 'react';
import api, { getErrorMessage } from '../api';
import { formatWhen } from '../format';

/** Saved conversations for this database; pick one to continue it. */
export default function ConversationsPanel({ databaseId, activeId, refreshKey = 0, onOpen, onNew, onError, disabled }) {
  const [conversations, setConversations] = useState([]);

  const load = useCallback(async () => {
    try {
      const response = await api.get(`/databases/${databaseId}/conversations`);
      setConversations(response.data);
    } catch (err) {
      onError?.(getErrorMessage(err, 'Could not load conversations.'));
    }
  }, [databaseId, onError]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const remove = async (conversation) => {
    if (!window.confirm(`Delete "${conversation.title}"?`)) return;
    try {
      await api.delete(`/databases/${databaseId}/conversations/${conversation.id}`);
      if (conversation.id === activeId) onNew();
      load();
    } catch (err) {
      onError?.(getErrorMessage(err, 'Could not delete the conversation.'));
    }
  };

  const rename = async (conversation) => {
    const title = window.prompt('Rename conversation', conversation.title);
    if (!title || title === conversation.title) return;
    try {
      await api.patch(`/databases/${databaseId}/conversations/${conversation.id}`, { title });
      load();
    } catch (err) {
      onError?.(getErrorMessage(err, 'Could not rename the conversation.'));
    }
  };

  return (
    <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-900">Conversations</h2>
        <button
          onClick={onNew}
          disabled={disabled}
          className="text-xs font-medium text-brand-700 hover:text-brand-800 disabled:opacity-50"
        >
          + New
        </button>
      </div>
      {conversations.length === 0 ? (
        <p className="text-xs text-gray-400">Your conversations will show up here.</p>
      ) : (
        <ul className="space-y-1 max-h-96 overflow-y-auto">
          {conversations.map((c) => (
            <li key={c.id} className="group flex items-center gap-1">
              <button
                onClick={() => onOpen(c.id)}
                disabled={disabled}
                title={c.title}
                className={`flex-1 min-w-0 text-left rounded-lg px-2 py-1.5 transition disabled:opacity-50 ${
                  c.id === activeId ? 'bg-brand-50 text-brand-800' : 'text-gray-600 hover:bg-brand-50 hover:text-brand-700'
                }`}
              >
                <span className="block text-sm truncate">{c.title}</span>
                <span className="block text-xs text-gray-400">{formatWhen(c.updated_at)}</span>
              </button>
              <div className="hidden group-hover:flex flex-col text-xs">
                <button onClick={() => rename(c)} className="text-gray-400 hover:text-brand-700" aria-label={`Rename ${c.title}`}>
                  ✎
                </button>
                <button onClick={() => remove(c)} className="text-gray-400 hover:text-red-500" aria-label={`Delete ${c.title}`}>
                  ✕
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
