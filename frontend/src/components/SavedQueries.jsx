import React from 'react';

/** The user's saved SQL for this database. */
export default function SavedQueries({ queries, onPick, onDelete }) {
  return (
    <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-4">
      <h2 className="text-sm font-semibold text-gray-900 mb-3">Saved queries</h2>
      {queries.length === 0 ? (
        <p className="text-xs text-gray-400">Save a query from the editor or from an answer to keep it here.</p>
      ) : (
        <ul className="space-y-1 max-h-80 overflow-y-auto">
          {queries.map((q) => (
            <li key={q.id} className="group flex items-center gap-1">
              <button
                onClick={() => onPick(q)}
                title={q.sql}
                className="flex-1 min-w-0 text-left text-sm text-gray-600 hover:bg-brand-50 hover:text-brand-700 rounded-lg px-2 py-1.5 truncate"
              >
                {q.name}
              </button>
              <button
                onClick={() => onDelete(q)}
                className="hidden group-hover:block text-xs text-gray-400 hover:text-red-500"
                aria-label={`Delete ${q.name}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
