import React, { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import AuthCard, { inputClass, primaryButtonClass } from '../components/AuthCard';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get('token') || '';
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (password !== confirmPassword) {
      setError('Passwords do not match');
      return;
    }
    setLoading(true);
    setError('');
    try {
      await api.post('/auth/password/reset', { token, new_password: password });
      navigate('/login', { state: { notice: 'Your password was reset. Sign in with the new one.' } });
    } catch (err) {
      setError(getErrorMessage(err, 'Could not reset the password.'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthCard
      title="Choose a new password"
      footer={<Link to="/forgot-password" className="text-brand-700 font-medium hover:underline">Need a new link?</Link>}
    >
      {!token ? (
        <Banner>This page needs the link from your reset email.</Banner>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Banner onDismiss={() => setError('')}>{error}</Banner>}
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">New password</label>
            <input id="password" type="password" minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} required />
          </div>
          <div>
            <label htmlFor="confirm" className="block text-sm font-medium text-gray-700 mb-1">Confirm new password</label>
            <input id="confirm" type="password" minLength={8} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className={inputClass} required />
          </div>
          <button type="submit" disabled={loading} className={primaryButtonClass}>
            {loading && <Spinner className="h-4 w-4" />} Reset password
          </button>
        </form>
      )}
    </AuthCard>
  );
}
