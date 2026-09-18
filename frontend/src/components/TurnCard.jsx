import React, { useState } from 'react';
import ReasoningSteps from './ReasoningSteps';
import ResultsTable from './ResultsTable';
import Spinner from './Spinner';

export default function TurnCard({
  turn,
  exportFormat,
  onExportFormatChange,
  onExport,
  onConfirmWrite,
  onDismissWrite,
}) {
  const [copied, setCopied] = useState(false);
  const [showDetails, setShowDetails] = useState(false);

  const copySql = async () => {
    if (!turn.sql) return;
    try {
      await navigator.clipboard.writeText(turn.sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable
    }
  };

  return (
    <div className="space-y-3">
      {/* question */}
      <div className="flex justify-end">
        <div className="bg-brand-600 text-white px-4 py-2.5 rounded-2xl rounded-br-md max-w-[85%] whitespace-pre-wrap text-sm">
          {turn.question}
        </div>
      </div>

      {/* answer */}
      <div className="bg-white rounded-2xl rounded-bl-md shadow-soft border border-gray-100 p-5 space-y-5">
        {turn.error ? (
          <p className="text-sm text-red-600">{turn.error}</p>
        ) : (
          <p className="text-gray-700 whitespace-pre-wrap text-sm min-h-[1.25rem]">
            {turn.answer}
            {turn.streaming && <span className="inline-block w-2 h-4 bg-brand-400 animate-pulse ml-0.5 align-text-bottom" />}
          </p>
        )}

        {turn.streaming && !turn.answer && turn.toolCalls.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-gray-400">
            <Spinner className="h-3 w-3" /> Thinking...
          </div>
        )}

        {turn.pendingWrite && (
          <div className="border border-red-200 bg-red-50 rounded-xl p-4 space-y-3">
            <div className="text-sm font-semibold text-red-700">This will modify your data</div>
            <pre className="bg-white border border-red-100 text-gray-800 p-3 rounded-lg text-xs overflow-x-auto">
              <code>{turn.pendingWrite.sql}</code>
            </pre>
            {turn.pendingWrite.error && <p className="text-xs text-red-600">{turn.pendingWrite.error}</p>}
            <div className="flex gap-2">
              <button
                onClick={() => onConfirmWrite(turn)}
                disabled={turn.pendingWrite.running}
                className="flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white text-sm font-medium px-4 py-2 rounded-lg disabled:opacity-50"
              >
                {turn.pendingWrite.running && <Spinner className="h-3 w-3" />} Run anyway
              </button>
              <button
                onClick={() => onDismissWrite(turn)}
                disabled={turn.pendingWrite.running}
                className="text-sm text-gray-600 hover:text-gray-900 px-3 py-2"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {turn.sql && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wide">SQL</h3>
              <button onClick={copySql} className="text-xs font-medium text-brand-700 hover:text-brand-800">
                {copied ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <pre className="bg-gray-900 text-brand-100 p-4 rounded-xl text-sm overflow-x-auto">
              <code>{turn.sql}</code>
            </pre>
          </div>
        )}

        {turn.result?.rows && (
          <ResultsTable
            columns={turn.result.columns}
            rows={turn.result.rows}
            exportFormat={exportFormat}
            onExportFormatChange={onExportFormatChange}
            onExport={() => onExport(turn)}
          />
        )}

        {turn.groundedOn && turn.groundedOn.length > 0 && (
          <div className="bg-brand-50 border border-brand-100 p-3 rounded-xl">
            <h3 className="text-xs font-semibold text-brand-800 mb-1">Grounded on</h3>
            {turn.groundedOn.map((ref, idx) => (
              <div key={idx} className="text-xs text-brand-700">
                {ref.source}
                {ref.chapter && ` — ${ref.chapter}`}
              </div>
            ))}
          </div>
        )}

        {turn.toolCalls.length > 0 && (
          <div>
            <button
              onClick={() => setShowDetails((v) => !v)}
              className="text-xs text-gray-400 hover:text-gray-600"
            >
              {showDetails ? 'Hide' : 'Show'} reasoning steps ({turn.toolCalls.length})
            </button>
            {showDetails && (
              <div className="mt-2">
                <ReasoningSteps toolCalls={turn.toolCalls} />
              </div>
            )}
          </div>
        )}

        {turn.elapsedMs != null && (
          <div className="text-xs text-gray-400 pt-3 border-t border-gray-100">
            Completed in {turn.elapsedMs}ms
          </div>
        )}
      </div>
    </div>
  );
}
