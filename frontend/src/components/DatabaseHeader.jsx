import React from 'react';
import { Link, NavLink } from 'react-router-dom';

const tabClass = ({ isActive }) =>
  `px-3 py-1.5 rounded-lg text-sm font-medium transition ${
    isActive ? 'bg-brand-600 text-white' : 'text-gray-600 hover:bg-brand-50 hover:text-brand-700'
  }`;

/** Database name plus the Ask / Data switch shared by both pages. */
export default function DatabaseHeader({ databaseId, database, actions }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <Link to="/databases" className="text-sm text-brand-700 hover:underline">
          ← All databases
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-1">{database?.name || 'Database'}</h1>
      </div>
      <div className="flex items-center gap-4">
        {actions}
        <nav className="flex gap-1 bg-white border border-gray-100 rounded-xl p-1 shadow-soft">
          <NavLink to={`/databases/${databaseId}/ask`} className={tabClass}>
            Ask
          </NavLink>
          <NavLink to={`/databases/${databaseId}/data`} className={tabClass}>
            Data
          </NavLink>
        </nav>
      </div>
    </div>
  );
}
