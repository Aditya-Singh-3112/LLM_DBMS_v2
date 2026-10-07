import React, { useCallback, useEffect, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import { useDatabase } from '../hooks/useDatabase';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import DatabaseHeader from '../components/DatabaseHeader';
import TablesPanel from '../components/TablesPanel';
import TableBrowser from '../components/TableBrowser';
import SqlEditor from '../components/SqlEditor';
import SavedQueries from '../components/SavedQueries';
import ActivityLog from '../components/ActivityLog';
import UndoBar from '../components/UndoBar';

const tabButton = (active) =>
  `px-4 py-2 text-sm font-medium border-b-2 transition ${
    active ? 'border-brand-600 text-brand-700' : 'border-transparent text-gray-500 hover:text-gray-800'
  }`;

export default function DataPage() {
  const { databaseId } = useParams();
  const { database, canWrite, isOwner } = useDatabase(databaseId);
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'tables';
  const table = params.get('table');

  const [error, setError] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);
  const [sql, setSql] = useState('');
  const [saved, setSaved] = useState([]);

  const refresh = () => setRefreshKey((k) => k + 1);
  const go = (next) => setParams({ tab, ...(table ? { table } : {}), ...next }, { replace: true });

  const loadSaved = useCallback(async () => {
    try {
      const response = await api.get(`/databases/${databaseId}/saved-queries`);
      setSaved(response.data);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not load saved queries.'));
    }
  }, [databaseId]);

  useEffect(() => {
    loadSaved();
  }, [loadSaved]);

  const saveQuery = async (text) => {
    const name = window.prompt('Name this query');
    if (!name) return;
    try {
      await api.post(`/databases/${databaseId}/saved-queries`, { name, sql: text });
      loadSaved();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not save the query.'));
    }
  };

  const deleteQuery = async (query) => {
    if (!window.confirm(`Delete saved query "${query.name}"?`)) return;
    try {
      await api.delete(`/databases/${databaseId}/saved-queries/${query.id}`);
      loadSaved();
    } catch (err) {
      setError(getErrorMessage(err, 'Could not delete the query.'));
    }
  };

  return (
    <Layout>
      <DatabaseHeader databaseId={databaseId} database={database} />

      <div className="space-y-4 mb-4">
        {error && (
          <Banner type="error" onDismiss={() => setError('')}>
            {error}
          </Banner>
        )}
        <UndoBar databaseId={databaseId} refreshKey={refreshKey} canWrite={canWrite} onUndone={refresh} onError={setError} />
      </div>

      <div className="flex gap-2 border-b border-gray-200 mb-6">
        <button className={tabButton(tab === 'tables')} onClick={() => go({ tab: 'tables' })}>
          Tables
        </button>
        <button className={tabButton(tab === 'sql')} onClick={() => go({ tab: 'sql' })}>
          SQL
        </button>
        {isOwner && (
          <button className={tabButton(tab === 'activity')} onClick={() => go({ tab: 'activity' })}>
            Activity
          </button>
        )}
      </div>

      {tab === 'tables' && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          <aside className="lg:col-span-1">
            <TablesPanel
              databaseId={databaseId}
              refreshKey={refreshKey}
              onError={setError}
              canWrite={canWrite}
              selected={table}
              onSelect={(name) => go({ table: name })}
              onImported={refresh}
            />
          </aside>
          <div className="lg:col-span-3">
            {table ? (
              <TableBrowser databaseId={databaseId} table={table} refreshKey={refreshKey} />
            ) : (
              <div className="bg-white rounded-2xl shadow-soft border border-gray-100 p-8 text-center text-gray-400 text-sm">
                Pick a table to see its columns and rows.
              </div>
            )}
          </div>
        </div>
      )}

      {tab === 'sql' && (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          <aside className="lg:col-span-1 order-2 lg:order-1">
            <SavedQueries queries={saved} onPick={(q) => setSql(q.sql)} onDelete={deleteQuery} />
          </aside>
          <div className="lg:col-span-3 order-1 lg:order-2">
            <SqlEditor databaseId={databaseId} sql={sql} onSqlChange={setSql} onSave={saveQuery} onWritten={refresh} />
          </div>
        </div>
      )}

      {tab === 'activity' && isOwner && <ActivityLog databaseId={databaseId} />}
    </Layout>
  );
}
