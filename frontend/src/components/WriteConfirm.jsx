import React from 'react';
import Spinner from './Spinner';
import { pluralize } from '../format';

/**
 * A write that was dry-run but not executed: what it will do, whether it
 * can be undone, and the buttons to run or cancel it.
 */
export default function WriteConfirm({ pending, onConfirm, onCancel }) {
  return (
    <div className="border border-red-200 bg-red-50 rounded-xl p-4 space-y-3">
      <div className="text-sm font-semibold text-red-700">This will modify your data</div>
      <pre className="bg-white border border-red-100 text-gray-800 p-3 rounded-lg text-xs overflow-x-auto">
        <code>{pending.sql}</code>
      </pre>
      <ul className="text-xs text-red-800 space-y-1">
        {pending.rows_affected != null && (
          <li>A dry run shows it affects {pluralize(pending.rows_affected, 'row')}.</li>
        )}
        {pending.undo_available === true && <li>You can undo it afterwards.</li>}
        {pending.undo_available === false && (
          <li className="font-medium">
            It can't be undone{pending.undo_unavailable_reason ? `: ${pending.undo_unavailable_reason}` : ''}.
          </li>
        )}
      </ul>
      {pending.error && <p className="text-xs text-red-600">{pending.error}</p>}
      <div className="flex gap-2">
        <button
          onClick={onConfirm}
          disabled={pending.running}
          className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-lg disabled:opacity-50"
        >
          {pending.running && <Spinner className="h-3 w-3" />} Run anyway
        </button>
        <button
          onClick={onCancel}
          disabled={pending.running}
          className="text-sm text-gray-600 hover:text-gray-900 px-3 py-2"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
