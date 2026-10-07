import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import AuthCard, { inputClass, primaryButtonClass } from '../components/AuthCard';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await api.post('/auth/password/forgot', { email });
      setSent(true);
    } catch (err) {
      setError(getErrorMessage(err, 'Could not send the email. Please try again.'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthCard
      title="Reset your password"
      subtitle="We'll email you a link to choose a new one"
      footer={<Link to="/login" className="text-brand-700 font-medium hover:underline">Back to sign in</Link>}
    >
      {sent ? (
        <Banner type="success">
          If an account uses {email}, a reset link is on its way. It expires in an hour.
        </Banner>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <Banner onDismiss={() => setError('')}>{error}</Banner>}
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
            <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} required />
          </div>
          <button type="submit" disabled={loading} className={primaryButtonClass}>
            {loading && <Spinner className="h-4 w-4" />} Send reset link
          </button>
        </form>
      )}
    </AuthCard>
  );
}
