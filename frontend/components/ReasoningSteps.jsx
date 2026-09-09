import React, { useState } from 'react';

function formatResult(result) {
  if (result == null) return '';
  if (typeof result === 'string') return result;
  try {
    return JSON.stringify(result, null, 2);
  } catch {
    return String(result);
  }
}

export default function ReasoningSteps({ toolCalls }) {
  const [openIdx, setOpenIdx] = useState(null);

  if (!toolCalls || toolCalls.length === 0) return null;

  return (
    <div>
      <h2 className="text-sm font-semibold text-gray-900 mb-2">
        Reasoning steps ({toolCalls.length})
      </h2>
      <div className="border border-gray-200 rounded-lg divide-y divide-gray-100 overflow-hidden">
        {toolCalls.map((call, idx) => {
          const isOpen = openIdx === idx;
          const failed = call.status !== 'success';
          return (
            <div key={idx}>
              <button
                onClick={() => setOpenIdx(isOpen ? null : idx)}
                className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-brand-50/60 transition"
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`h-2 w-2 rounded-full flex-shrink-0 ${failed ? 'bg-red-500' : 'bg-brand-500'}`} />
                  <span className="font-medium text-gray-900 truncate">{call.tool_name}</span>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 flex-shrink-0">
                    {call.source}
                  </span>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className="text-xs text-gray-400">{call.duration_ms}ms</span>
                  <svg
                    className={`h-4 w-4 text-gray-400 transition-transform ${isOpen ? 'rotate-180' : ''}`}
                    viewBox="0 0 20 20"
                    fill="currentColor"
                  >
                    <path
                      fillRule="evenodd"
                      d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z"
                      clipRule="evenodd"
                    />
                  </svg>
                </div>
              </button>
              {isOpen && (
                <div className="px-4 pb-4 space-y-2 bg-gray-50">
                  {call.args && Object.keys(call.args).length > 0 && (
                    <div>
                      <div className="text-xs font-medium text-gray-500 mb-1">Args</div>
                      <pre className="text-xs bg-white border border-gray-200 rounded p-2 overflow-x-auto">
                        {JSON.stringify(call.args, null, 2)}
                      </pre>
                    </div>
                  )}
                  <div>
                    <div className="text-xs font-medium text-gray-500 mb-1">Result</div>
                    <pre className="text-xs bg-white border border-gray-200 rounded p-2 overflow-x-auto whitespace-pre-wrap">
                      {formatResult(call.result)}
                    </pre>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}