import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import api, { getErrorMessage } from '../api';
import { useAuthStore } from '../store';
import Layout from '../components/Layout';
import Banner from '../components/Banner';
import Spinner from '../components/Spinner';
import { inputClass } from '../components/AuthCard';

const card = 'bg-white rounded-2xl shadow-soft border border-gray-100 p-6 space-y-4';

export default function AccountPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const setAccessToken = useAuthStore((state) => state.setAccessToken);
  const logout = useAuthStore((state) => state.logout);

  const [message, setMessage] = useState(null);
  const [usage, setUsage] = useState(null);
  const [passwords, setPasswords] = useState({ current: '', next: '' });
  const [deletePassword, setDeletePassword] = useState('');
  const [busy, setBusy] = useState('');

  useEffect(() => {
    api.get('/auth/me/usage').then((r) => setUsage(r.data)).catch(() => {});
  }, []);

  const act = async (name, fn) => {
    setBusy(name);
    setMessage(null);
    try {
      await fn();
    } catch (err) {
      setMessage({ type: 'error', text: getErrorMessage(err) });
    } finally {
      setBusy('');
    }
  };

  const resend = () =>
    act('resend', async () => {
      const r = await api.post('/auth/verify-email/resend');
      setMessage({ type: 'success', text: r.data.message });
    });

  const changePassword = (e) => {
    e.preventDefault();
    act('password', async () => {
      const r = await api.post('/auth/password/change', {
        current_password: passwords.current,
        new_password: passwords.next,
      });
      setAccessToken(r.data.access_token);
      setPasswords({ current: '', next: '' });
      setMessage({ type: 'success', text: 'Password changed. Your other sessions were signed out.' });
    });
  };

  const deleteAccount = (e) => {
    e.preventDefault();
    if (!window.confirm('Delete your account and every database you own? This cannot be undone.')) return;
    act('delete', async () => {
      await api.delete('/auth/me', { data: { password: deletePassword } });
      await logout();
      navigate('/login', { state: { notice: 'Your account was deleted.' } });
    });
  };

  const today = usage?.days?.[0];

  return (
    <Layout>
      <div className="max-w-2xl space-y-6">
        <h1 className="text-2xl font-bold text-gray-900">Account</h1>
        {message && (
          <Banner type={message.type} onDismiss={() => setMessage(null)}>
            {message.text}
          </Banner>
        )}

        <section className={card}>
          <h2 className="text-sm font-semibold text-gray-900">Email</h2>
          <div className="flex items-center justify-between gap-3">
            <span className="text-gray-700">{user?.email}</span>
            {user?.email_verified ? (
              <span className="text-xs font-medium px-2 py-1 rounded-full bg-brand-100 text-brand-700">Verified</span>
            ) : (
              <button
                onClick={resend}
                disabled={busy === 'resend'}
                className="text-sm font-medium text-brand-700 hover:text-brand-800 disabled:opacity-50"
              >
                Resend verification email
              </button>
            )}
          </div>
          {!user?.email_verified && (
            <p className="text-xs text-gray-500">Others can only share databases with you once your address is verified.</p>
          )}
        </section>

        <section className={card}>
          <h2 className="text-sm font-semibold text-gray-900">Change password</h2>
          <form onSubmit={changePassword} className="space-y-3">
            <input
              type="password"
              placeholder="Current password"
              aria-label="Current password"
              value={passwords.current}
              onChange={(e) => setPasswords({ ...passwords, current: e.target.value })}
              className={inputClass}
              required
            />
            <input
              type="password"
              placeholder="New password (8+ characters)"
              aria-label="New password"
              minLength={8}
              value={passwords.next}
              onChange={(e) => setPasswords({ ...passwords, next: e.target.value })}
              className={inputClass}
              required
            />
            <button
              type="submit"
              disabled={busy === 'password'}
              className="flex items-center gap-2 bg-brand-600 hover:bg-brand-700 text-white font-medium px-5 py-2 rounded-xl text-sm disabled:opacity-50"
            >
              {busy === 'password' && <Spinner className="h-4 w-4" />} Change password
            </button>
          </form>
        </section>

        <section className={card}>
          <h2 className="text-sm font-semibold text-gray-900">Assistant usage</h2>
          {!usage ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : (
            <>
              <p className="text-sm text-gray-600">
                Today: {today ? `${today.requests} questions, ${(today.input_tokens + today.output_tokens).toLocaleString()} tokens` : 'nothing yet'}
                {usage.daily_question_limit ? ` · limit ${usage.daily_question_limit} questions/day` : ''}
                {usage.daily_token_limit ? ` · limit ${usage.daily_token_limit.toLocaleString()} tokens/day` : ''}
              </p>
              {usage.days.length > 0 && (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-gray-500">
                      <th className="font-medium py-1">Day</th>
                      <th className="font-medium py-1 text-right">Questions</th>
                      <th className="font-medium py-1 text-right">Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.days.map((d) => (
                      <tr key={d.day} className="border-t border-gray-100">
                        <td className="py-1 text-gray-700">{d.day}</td>
                        <td className="py-1 text-right text-gray-700">{d.requests}</td>
                        <td className="py-1 text-right text-gray-700">{(d.input_tokens + d.output_tokens).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </section>

        <section className={`${card} border-red-200`}>
          <h2 className="text-sm font-semibold text-red-700">Delete account</h2>
          <p className="text-sm text-gray-600">
            Deletes your account, every database you own (including the access you gave others), and your access to databases shared with you.
          </p>
          <form onSubmit={deleteAccount} className="flex gap-2">
            <input
              type="password"
              placeholder="Your password"
              aria-label="Password to confirm deletion"
              value={deletePassword}
              onChange={(e) => setDeletePassword(e.target.value)}
              className={inputClass}
              required
            />
            <button
              type="submit"
              disabled={busy === 'delete'}
              className="flex-shrink-0 flex items-center gap-2 bg-red-600 hover:bg-red-700 text-white font-medium px-4 rounded-xl text-sm disabled:opacity-50"
            >
              {busy === 'delete' && <Spinner className="h-4 w-4" />} Delete
            </button>
          </form>
        </section>
      </div>
    </Layout>
  );
}
