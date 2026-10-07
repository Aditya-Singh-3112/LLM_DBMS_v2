import React, { useEffect, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import { useAuthStore } from '../store';
import AuthCard from '../components/AuthCard';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const refreshUser = useAuthStore((state) => state.refreshUser);
  const [state, setState] = useState({ status: token ? 'working' : 'error', message: 'This page needs the link from your verification email.' });
  const sent = useRef(false);

  useEffect(() => {
    // Tokens are single-use; don't spend it twice under StrictMode.
    if (!token || sent.current) return;
    sent.current = true;
    api
      .post('/auth/verify-email', { token })
      .then(() => {
        setState({ status: 'done' });
        refreshUser();
      })
      .catch((err) => setState({ status: 'error', message: getErrorMessage(err, 'This link is invalid or has expired.') }));
  }, [token, refreshUser]);

  return (
    <AuthCard
      title="Verify your email"
      footer={
        <Link to={isAuthenticated ? '/databases' : '/login'} className="text-brand-700 font-medium hover:underline">
          {isAuthenticated ? 'Go to your databases' : 'Sign in'}
        </Link>
      }
    >
      {state.status === 'working' && (
        <div className="flex items-center gap-2 text-gray-500 text-sm">
          <Spinner className="h-4 w-4" /> Verifying…
        </div>
      )}
      {state.status === 'done' && <Banner type="success">Your email address is verified. Others can now share databases with you.</Banner>}
      {state.status === 'error' && (
        <Banner>
          {state.message} {isAuthenticated && <>You can send a new link from your <Link to="/account" className="underline">account page</Link>.</>}
        </Banner>
      )}
    </AuthCard>
  );
}
