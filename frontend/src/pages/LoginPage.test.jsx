import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LoginPage from './LoginPage';

jest.mock('../api', () => ({ __esModule: true, default: { post: jest.fn() }, getErrorMessage: () => '' }));

test('links to password reset and shows notices from other pages', () => {
  render(
    <MemoryRouter initialEntries={[{ pathname: '/login', state: { notice: 'Your password was reset.' } }]}>
      <LoginPage />
    </MemoryRouter>
  );
  expect(screen.getByRole('link', { name: 'Forgot password?' })).toHaveAttribute('href', '/forgot-password');
  expect(screen.getByText('Your password was reset.')).toBeInTheDocument();
});
