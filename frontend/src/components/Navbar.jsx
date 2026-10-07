import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useAuthStore } from '../store';

export default function Navbar() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const logout = useAuthStore((state) => state.logout);

  return (
    <nav className="bg-white border-b border-brand-100 sticky top-0 z-10">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-3 flex items-center justify-between">
        <Link to="/databases" className="flex items-center gap-2">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-white font-bold">
            Q
          </span>
          <span className="text-lg font-semibold text-gray-900">LLM-DBMS</span>
        </Link>
        <div className="flex items-center gap-4">
          {user?.email && (
            <Link to="/account" className="hidden sm:flex items-center gap-2 text-sm text-gray-500 hover:text-brand-700">
              {user.email}
              {!user.email_verified && (
                <span className="text-xs font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-700">verify email</span>
              )}
            </Link>
          )}
          <Link to="/account" className="sm:hidden text-sm font-medium text-gray-600 hover:text-brand-700">
            Account
          </Link>
          <button
            onClick={async () => {
              await logout();
              navigate('/login');
            }}
            className="text-sm font-medium text-gray-600 hover:text-brand-700 transition"
          >
            Logout
          </button>
        </div>
      </div>
    </nav>
  );
}