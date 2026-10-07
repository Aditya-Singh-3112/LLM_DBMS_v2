import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import api from '../api';
import SqlEditor from './SqlEditor';

jest.mock('../api', () => ({
  __esModule: true,
  default: { post: jest.fn() },
  getErrorMessage: (err, fallback) => err?.response?.data?.detail || fallback,
}));

function Harness({ onWritten }) {
  const [sql, setSql] = useState('');
  return <SqlEditor databaseId="db1" sql={sql} onSqlChange={setSql} onSave={() => {}} onWritten={onWritten} />;
}

afterEach(() => jest.resetAllMocks());

test('runs a SELECT and shows rows', async () => {
  api.post.mockResolvedValueOnce({ data: { columns: ['n'], rows: [[3]], truncated: false } });
  render(<Harness />);
  await userEvent.type(screen.getByLabelText('SQL'), 'SELECT count(*) AS n FROM t');
  await userEvent.click(screen.getByRole('button', { name: 'Run' }));

  expect(api.post).toHaveBeenCalledWith('/databases/db1/sql', { sql: 'SELECT count(*) AS n FROM t' });
  expect(await screen.findByText('3')).toBeInTheDocument();
});

test('asks before a write, then confirms it', async () => {
  const onWritten = jest.fn();
  api.post
    .mockRejectedValueOnce({
      response: {
        status: 409,
        data: { detail: { code: 'confirmation_required', sql: 'DELETE FROM t', rows_affected: 2, undo_available: true } },
      },
    })
    .mockResolvedValueOnce({ data: { columns: [], rows: [], rows_affected: 2, undo_available: true } });

  render(<Harness onWritten={onWritten} />);
  await userEvent.type(screen.getByLabelText('SQL'), 'DELETE FROM t');
  await userEvent.click(screen.getByRole('button', { name: 'Run' }));
  expect(await screen.findByText(/affects 2 rows/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Run anyway/ }));
  expect(api.post).toHaveBeenLastCalledWith('/databases/db1/sql/confirm', { sql: 'DELETE FROM t' });
  expect(await screen.findByText(/Done: 2 rows affected/)).toBeInTheDocument();
  expect(onWritten).toHaveBeenCalled();
});

test('shows query errors', async () => {
  api.post.mockRejectedValueOnce({ response: { status: 400, data: { detail: 'Query failed: column "x" does not exist' } } });
  render(<Harness />);
  await userEvent.type(screen.getByLabelText('SQL'), 'SELECT x');
  await userEvent.click(screen.getByRole('button', { name: 'Run' }));
  expect(await screen.findByText(/column "x" does not exist/)).toBeInTheDocument();
});
