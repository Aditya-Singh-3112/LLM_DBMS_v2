import { getErrorMessage } from './api';

test('reads string, validation-list and missing details', () => {
  expect(getErrorMessage({ response: { data: { detail: 'Nope' } } })).toBe('Nope');
  expect(
    getErrorMessage({ response: { data: { detail: [{ msg: 'field required' }, { msg: 'too short' }] } } })
  ).toBe('field required, too short');
  expect(getErrorMessage({}, 'fallback')).toBe('fallback');
  // Structured details (e.g. confirmation_required) fall back to the default text.
  expect(getErrorMessage({ response: { data: { detail: { code: 'x' } } } }, 'fallback')).toBe('fallback');
});
