/**
 * Login page — username + password.
 *
 * Demo creds are surfaced in-page so a reviewer can sign in without digging
 * through the README. Remove the hint block before any real deployment.
 */
import React, { useState } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { LogIn, AlertCircle } from 'lucide-react';
import { useAuth } from '../auth/AuthContext';

export default function Login() {
  const { login, loading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from;

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    try {
      const u = await login(username.trim(), password);
      // Admins land on the checker queue; regular users land on intake.
      const target = from || (u.role === 'ADMIN' ? '/checker' : '/');
      navigate(target, { replace: true });
    } catch (err) {
      setError(err?.response?.data?.detail || 'Login failed.');
    }
  };

  return (
    <div style={{ maxWidth: 420, margin: '0 auto' }}>
      <div className="card">
        <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Sign in</h2>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
          Authenticate to access the IASW workflow.
        </p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Username</label>
            <input
              type="text" className="form-control"
              value={username} onChange={(e) => setUsername(e.target.value)}
              autoComplete="username" autoFocus required
            />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input
              type="password" className="form-control"
              value={password} onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password" required
            />
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem' }}
            disabled={loading || !username || !password}
          >
            <LogIn size={16} style={{ marginRight: 8 }} />
            {loading ? 'Signing in…' : 'Sign in'}
          </button>

          {error && (
            <div style={{
              marginTop: '1rem', padding: '0.75rem',
              background: 'rgba(239, 68, 68, 0.1)',
              color: 'var(--danger-color)',
              borderRadius: '0.5rem',
              display: 'flex', alignItems: 'flex-start', gap: '0.5rem',
              fontSize: '0.875rem',
            }}>
              <AlertCircle size={18} style={{ flexShrink: 0 }} />
              <span>{error}</span>
            </div>
          )}
        </form>

        <div style={{
          marginTop: '1.5rem', paddingTop: '1rem',
          borderTop: '1px solid var(--border-color)',
          fontSize: '0.8rem', color: 'var(--text-secondary)',
        }}>
          New user? <Link to="/register" style={{ color: 'var(--primary-color)' }}>Request an account</Link>{' '}
          (admin approval required).
        </div>

        <div style={{
          marginTop: '1rem', padding: '0.75rem',
          background: 'rgba(255,255,255,0.04)',
          border: '1px solid var(--border-color)',
          borderRadius: '0.5rem',
          fontSize: '0.75rem', color: 'var(--text-secondary)',
          fontFamily: 'monospace',
        }}>
          <div style={{ fontWeight: 600, marginBottom: '0.25rem' }}>Demo credentials</div>
          <div>admin / admin123</div>
          <div>user  / user123</div>
        </div>
      </div>
    </div>
  );
}
