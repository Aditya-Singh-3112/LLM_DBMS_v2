import { useEffect } from 'react';
import { useDatabaseStore } from '../store';
import api from '../api';

/**
 * The database a route refers to. The store is in-memory only, so after a
 * reload we no longer know it; look it up.
 */
export function useDatabase(databaseId) {
  const selectedDatabase = useDatabaseStore((state) => state.selectedDatabase);
  const setSelectedDatabase = useDatabaseStore((state) => state.setSelectedDatabase);

  useEffect(() => {
    if (selectedDatabase?.id === databaseId) return;
    let cancelled = false;
    api
      .get('/databases')
      .then((response) => {
        if (cancelled) return;
        const match = response.data.find((db) => db.id === databaseId);
        if (match) setSelectedDatabase(match);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [databaseId, selectedDatabase, setSelectedDatabase]);

  const database = selectedDatabase?.id === databaseId ? selectedDatabase : null;
  const level = database?.access_level;
  return {
    database,
    canWrite: level === 'owner' || level === 'write',
    isOwner: level === 'owner',
  };
}
