import { turnsFromMessages } from './AskPage';

jest.mock('../api', () => ({ __esModule: true, default: {}, getErrorMessage: () => '' }));

test('rebuilds turns from stored messages', () => {
  const turns = turnsFromMessages([
    { role: 'human', content: 'How many?' },
    { role: 'ai', content: 'Three.', sql: 'SELECT count(*) FROM t' },
    { role: 'human', content: 'And now?' },
    { role: 'ai', content: 'Two.', sql: null },
  ]);
  expect(turns.map((t) => [t.question, t.answer, t.sql])).toEqual([
    ['How many?', 'Three.', 'SELECT count(*) FROM t'],
    ['And now?', 'Two.', null],
  ]);
  expect(new Set(turns.map((t) => t.id)).size).toBe(2);
});
