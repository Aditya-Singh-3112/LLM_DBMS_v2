import React, { useEffect } from 'react';
import {
  BrowserRouter as Router,
  Routes,
  Route,
  Navigate,
} from 'react-router-dom';
import { useAuthStore } from './store';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import DatabasesPage from './pages/DatabasesPage';
import AskPage from './pages/AskPage';
import DataPage from './pages/DataPage';
import AccountPage from './pages/AccountPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import VerifyEmailPage from './pages/VerifyEmailPage';
import Spinner from './components/Spinner';

function PrivateRoute({ children }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated ? children : <Navigate to="/login" />;
}

export default function App() {
  const bootstrapping = useAuthStore((state) => state.bootstrapping);
  const bootstrap = useAuthStore((state) => state.bootstrap);

  useEffect(() => {
    bootstrap();
  }, [bootstrap]);

  if (bootstrapping) {
    return (
      <div className="min-h-screen flex items-center justify-center text-gray-400 gap-2">
        <Spinner /> Loading…
      </div>
    );
  }

  return (
    <Router>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route
          path="/account"
          element={
            <PrivateRoute>
              <AccountPage />
            </PrivateRoute>
          }
        />
        <Route
          path="/databases/:databaseId/data"
          element={
            <PrivateRoute>
              <DataPage />
            </PrivateRoute>
          }
        />
        <Route
          path="/databases"
          element={
            <PrivateRoute>
              <DatabasesPage />
            </PrivateRoute>
          }
        />
        <Route
          path="/databases/:databaseId/ask"
          element={
            <PrivateRoute>
              <AskPage />
            </PrivateRoute>
          }
        />
        <Route path="/" element={<Navigate to="/databases" />} />
      </Routes>
    </Router>
  );
}