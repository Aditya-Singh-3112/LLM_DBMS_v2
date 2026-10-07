import { formatBytes, pluralize } from './format';

test('formatBytes', () => {
  expect(formatBytes(0)).toBe('0 B');
  expect(formatBytes(1536)).toBe('1.5 KB');
  expect(formatBytes(500 * 1024 * 1024)).toBe('500 MB');
  expect(formatBytes(null)).toBe('');
});

test('pluralize', () => {
  expect(pluralize(1, 'row')).toBe('1 row');
  expect(pluralize(3, 'row')).toBe('3 rows');
});
