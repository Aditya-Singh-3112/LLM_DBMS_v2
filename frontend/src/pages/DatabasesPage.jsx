import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDatabaseStore } from '../store';
import api, { getErrorMessage } from '../api';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';

const accessBadge = {
  owner: 'bg-brand-100 text-brand-700',
  write: 'bg-blue-100 text-blue-700',
  read: 'bg-gray-100 text-gray-600',
};

export default function DatabasesPage() {
  const navigate = useNavigate();
  const databases = useDatabaseStore((state) => state.databases);
  const setDatabases = useDatabaseStore((state) => state.setDatabases);
  const setSelectedDatabase = useDatabaseStore((state) => state.setSelectedDatabase);

  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchDatabases();
  }, []);

  const fetchDatabases = async () => {
    try {
      const response = await api.get('/databases');
      setDatabases(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to load databases.'));
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError('');

    try {
      const response = await api.post('/databases', { name: newName.trim() });
      setDatabases([...databases, response.data]);
      setNewName('');
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to create database.'));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (databaseId) => {
    if (!window.confirm('Delete this database? This cannot be undone.')) return;

    try {
      await api.delete(`/databases/${databaseId}`);
      setDatabases(databases.filter((db) => db.id !== databaseId));
    } catch (err) {
      setError(getErrorMessage(err, 'Failed to delete database.'));
    }
  };

  const handleSelect = (database) => {
    setSelectedDatabase(database);
    navigate(`/databases/${database.id}/ask`);
  };

  return (
    <Layout>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Your databases</h1>
        <p className="text-sm text-gray-500 mt-1">Pick one to start asking questions.</p>
      </div>

      {error && (
        <div className="mb-6">
          <Banner type="error" onDismiss={() => setError('')}>
            {error}
          </Banner>
        </div>
      )}

      <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-6 mb-8">
        <h2 className="text-sm font-semibold text-gray-900 mb-3">Create a new database</h2>
        <form onSubmit={handleCreate} className="flex gap-3">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. sales_analytics"
            className="flex-1 px-4 py-2.5 border border-gray-200 rounded-xl focus:ring-2 focus:ring-brand-500 focus:border-transparent outline-none transition"
            required
          />
          <button
            type="submit"
            disabled={creating}
            className="bg-brand-600 hover:bg-brand-700 text-white font-medium px-6 py-2.5 rounded-xl transition disabled:opacity-50 flex items-center gap-2"
          >
            {creating && <Spinner className="h-4 w-4" />}
            Create
          </button>
        </form>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 text-gray-400 py-16">
          <Spinner /> Loading databases...
        </div>
      ) : databases.length === 0 ? (
        <div className="text-center py-16 text-gray-400">
          <p>No databases yet. Create your first one above.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {databases.map((db) => (
            <div
              key={db.id}
              className="bg-white rounded-2xl shadow-soft border border-gray-100 p-6 flex flex-col hover:shadow-md transition"
            >
              <div className="flex items-start justify-between mb-3">
                <h3 className="text-lg font-semibold text-gray-900 truncate">{db.name}</h3>
                <span
                  className={`text-xs font-medium px-2 py-1 rounded-full flex-shrink-0 ${
                    accessBadge[db.access_level] || 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {db.access_level}
                </span>
              </div>
              <p className="text-xs text-gray-400 mb-6">
                Created {new Date(db.created_at).toLocaleDateString()}
              </p>
              <div className="mt-auto flex gap-2">
                <button
                  onClick={() => handleSelect(db)}
                  className="flex-1 bg-brand-600 hover:bg-brand-700 text-white font-medium py-2 rounded-xl transition"
                >
                  Query
                </button>
                {db.access_level === 'owner' && (
                  <button
                    onClick={() => handleDelete(db.id)}
                    className="bg-red-50 hover:bg-red-100 text-red-600 font-medium px-4 py-2 rounded-xl transition"
                  >
                    Delete
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}