import React from 'react';

export default function ResultsTable({ columns, rows, exportFormat, onExportFormatChange, onExport }) {
  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-900">
          Results <span className="text-gray-400 font-normal">({rows.length} rows)</span>
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={exportFormat}
            onChange={(e) => onExportFormatChange(e.target.value)}
            className="text-sm border border-gray-200 rounded-lg px-2 py-1.5 bg-white"
          >
            <option value="csv">CSV</option>
            <option value="xlsx">XLSX</option>
          </select>
          <button
            onClick={onExport}
            className="text-sm font-medium bg-brand-600 hover:bg-brand-700 text-white px-3 py-1.5 rounded-lg transition"
          >
            Export
          </button>
        </div>
      </div>

      <div className="overflow-auto max-h-96 border border-gray-200 rounded-lg">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-brand-50">
            <tr>
              {columns.map((col, idx) => (
                <th
                  key={idx}
                  className="text-left font-medium text-gray-700 px-3 py-2 border-b border-gray-200 whitespace-nowrap"
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rows.map((row, rowIdx) => (
              <tr key={rowIdx} className="hover:bg-brand-50/40">
                {row.map((cell, cellIdx) => (
                  <td key={cellIdx} className="px-3 py-2 text-gray-700 whitespace-nowrap">
                    {cell === null ? <span className="text-gray-300 italic">null</span> : String(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}