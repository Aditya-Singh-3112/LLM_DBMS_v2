import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDatabaseStore, useAuthStore } from '../store';
import api from '../api';

export default function DatabasesPage() {
  const navigate = useNavigate();
  const databases = useDatabaseStore((state) => state.databases);
  const setDatabases = useDatabaseStore((state) => state.setDatabases);
  const setSelectedDatabase = useDatabaseStore(
    (state) => state.setSelectedDatabase
  );
  const logout = useAuthStore((state) => state.logout);

  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchDatabases();
  }, []);

  const fetchDatabases = async () => {
    try {
      const response = await api.get('/databases');
      setDatabases(response.data);
    } catch (err) {
      setError('Failed to load databases');
    } finally {
      setLoading(false);
    }
  };

  const handleCreate = async (e) => {
    e.preventDefault();
    if (!newName.trim()) return;

    try {
      const response = await api.post('/databases', { name: newName });
      setDatabases([...databases, response.data]);
      setNewName('');
    } catch (err) {
      setError(err.response?.data?.detail || 'Failed to create database');
    }
  };

  const handleDelete = async (databaseId) => {
    if (!window.confirm('Are you sure?')) return;

    try {
      await api.delete(`/databases/${databaseId}`);
      setDatabases(databases.filter((db) => db.id !== databaseId));
    } catch (err) {
      setError('Failed to delete database');
    }
  };

  const handleSelect = (database) => {
    setSelectedDatabase(database);
    navigate(`/databases/${database.id}/ask`);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="bg-white shadow">
        <div className="max-w-7xl mx-auto px-4 py-4 flex justify-between items-center">
          <h1 className="text-2xl font-bold text-gray-900">
            LLM-Powered DBMS
          </h1>
          <button
            onClick={() => {
              logout();
              navigate('/login');
            }}
            className="text-gray-600 hover:text-gray-900"
          >
            Logout
          </button>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto p-8">
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded">
            {error}
          </div>
        )}

        <div className="bg-white rounded-lg shadow p-6 mb-8">
          <h2 className="text-xl font-bold text-gray-900 mb-4">
            Create New Database
          </h2>
          <form onSubmit={handleCreate} className="flex gap-4">
            <input
              type="text"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Database name"
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg"
              required
            />
            <button
              type="submit"
              className="bg-blue-600 hover:bg-blue-700 text-white font-medium px-6 py-2 rounded-lg"
            >
              Create
            </button>
          </form>
        </div>

        {loading ? (
          <div className="text-center text-gray-500">Loading...</div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {databases.map((db) => (
              <div key={db.id} className="bg-white rounded-lg shadow p-6">
                <h3 className="text-lg font-bold text-gray-900 mb-2">
                  {db.name}
                </h3>
                <p className="text-sm text-gray-600 mb-4">
                  {db.access_level === 'owner' ? 'Owner' : db.access_level}
                </p>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleSelect(db)}
                    className="flex-1 bg-blue-600 hover:bg-blue-700 text-white font-medium py-2 rounded"
                  >
                    Query
                  </button>
                  {db.access_level === 'owner' && (
                    <button
                      onClick={() => handleDelete(db.id)}
                      className="bg-red-600 hover:bg-red-700 text-white font-medium px-4 py-2 rounded"
                    >
                      Delete
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}