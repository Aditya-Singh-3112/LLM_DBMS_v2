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

function PrivateRoute({ children }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  return isAuthenticated ? children : <Navigate to="/login" />;
}

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
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