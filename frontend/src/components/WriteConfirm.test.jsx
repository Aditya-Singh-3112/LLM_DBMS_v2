import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import WriteConfirm from './WriteConfirm';

test('shows the dry-run effect and undo availability', async () => {
  const onConfirm = jest.fn();
  const onCancel = jest.fn();
  render(
    <WriteConfirm
      pending={{ sql: 'DELETE FROM t', rows_affected: 3, undo_available: true }}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
  expect(screen.getByText('DELETE FROM t')).toBeInTheDocument();
  expect(screen.getByText(/affects 3 rows/)).toBeInTheDocument();
  expect(screen.getByText(/You can undo it afterwards/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole('button', { name: /Run anyway/ }));
  expect(onConfirm).toHaveBeenCalled();
  await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
  expect(onCancel).toHaveBeenCalled();
});

test('explains why a change cannot be undone', () => {
  render(
    <WriteConfirm
      pending={{ sql: 'DROP TABLE t', rows_affected: 1, undo_available: false, undo_unavailable_reason: 'too large' }}
      onConfirm={() => {}}
      onCancel={() => {}}
    />
  );
  expect(screen.getByText(/affects 1 row\./)).toBeInTheDocument();
  expect(screen.getByText("It can't be undone: too large.")).toBeInTheDocument();
});
