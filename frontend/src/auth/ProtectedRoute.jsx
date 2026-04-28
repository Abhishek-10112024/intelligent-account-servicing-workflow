/**
 * ProtectedRoute — guards a route by login state and optional role.
 *
 * Usage:
 *   <Route element={<ProtectedRoute><Intake /></ProtectedRoute>} />
 *   <Route element={<ProtectedRoute role="ADMIN"><Checker /></ProtectedRoute>} />
 */
import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';

export default function ProtectedRoute({ children, role }) {
  const { user } = useAuth();
  const location = useLocation();

  if (!user) {
    // Preserve the path they tried to hit so we can bounce back post-login.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  if (role && user.role !== role) {
    return (
      <div className="card empty-state" style={{ padding: '3rem', textAlign: 'center' }}>
        <h3>403 — Access denied</h3>
        <p style={{ marginTop: '0.75rem', color: 'var(--text-secondary)' }}>
          This page requires the <strong>{role}</strong> role. You're signed in as{' '}
          <strong>{user.username}</strong> ({user.role}).
        </p>
      </div>
    );
  }
  return children;
}
