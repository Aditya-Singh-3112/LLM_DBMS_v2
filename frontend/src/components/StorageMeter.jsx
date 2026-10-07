import React, { useEffect, useState } from 'react';
import api from '../api';
import { formatBytes } from '../format';

/** How much of its storage limit the database uses. */
export default function StorageMeter({ databaseId, refreshKey = 0 }) {
  const [usage, setUsage] = useState(null);

  useEffect(() => {
    api
      .get(`/databases/${databaseId}/usage`)
      .then((response) => setUsage(response.data))
      .catch(() => setUsage(null));
  }, [databaseId, refreshKey]);

  if (!usage) return null;
  const { size_bytes: size, limit_bytes: limit } = usage;
  const percent = limit ? Math.min(100, Math.round((size / limit) * 100)) : null;

  return (
    <div className="text-xs text-gray-500" title="Tables and indexes">
      <div className="flex justify-between mb-1">
        <span>Storage</span>
        <span>
          {formatBytes(size)}
          {limit ? ` of ${formatBytes(limit)}` : ''}
        </span>
      </div>
      {percent != null && (
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
          <div
            className={`h-full ${percent >= 90 ? 'bg-red-500' : percent >= 70 ? 'bg-amber-500' : 'bg-brand-500'}`}
            style={{ width: `${percent}%` }}
          />
        </div>
      )}
    </div>
  );
}
