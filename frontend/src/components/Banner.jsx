import React from 'react';

const styles = {
  error: 'bg-red-50 border-red-200 text-red-700',
  success: 'bg-brand-50 border-brand-200 text-brand-800',
  info: 'bg-gray-50 border-gray-200 text-gray-700',
};

export default function Banner({ type = 'error', children, onDismiss }) {
  return (
    <div className={`flex items-start justify-between gap-3 border px-4 py-3 rounded-lg text-sm ${styles[type]}`}>
      <span>{children}</span>
      {onDismiss && (
        <button onClick={onDismiss} className="text-current opacity-60 hover:opacity-100">
          ✕
        </button>
      )}
    </div>
  );
}