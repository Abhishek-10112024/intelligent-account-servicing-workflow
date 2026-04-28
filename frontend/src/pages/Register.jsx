/**
 * Register page — self-serve registration request.
 *
 * Submitting this form creates a PENDING registration. An admin must approve
 * it from the Checker screen before the account becomes usable.
 */
import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { UserPlus, AlertCircle, CheckCircle2 } from 'lucide-react';
import api from '../api';

export default function Register() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm]   = useState('');
  const [status, setStatus]     = useState(null); // 'submitted' | null
  const [error, setError]       = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 6) {
      setError('Password must be at least 6 characters.');
      return;
    }
    setSubmitting(true);
    try {
      await api.post('/api/auth/register', { username: username.trim(), password });
      setStatus('submitted');
    } catch (err) {
      setError(err?.response?.data?.detail || 'Registration failed.');
    } finally {
      setSubmitting(false);
    }
  };

  if (status === 'submitted') {
    return (
      <div style={{ maxWidth: 420, margin: '0 auto' }}>
        <div className="card">
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
            <CheckCircle2 size={22} color="var(--success-color)" />
            <h2 style={{ fontSize: '1.2rem' }}>Request received</h2>
          </div>
          <p style={{ color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            Your registration is awaiting administrator approval. You'll be able to sign in
            once an admin approves the request.
          </p>
          <div style={{ marginTop: '1.5rem' }}>
            <Link to="/login" className="btn btn-primary" style={{ textDecoration: 'none' }}>
              Back to sign in
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 420, margin: '0 auto' }}>
      <div className="card">
        <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Request an account</h2>
        <p style={{ color: 'var(--text-secondary)', marginBottom: '1.5rem' }}>
          An administrator will review and approve your request.
        </p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Username</label>
            <input
              type="text" className="form-control"
              value={username} onChange={(e) => setUsername(e.target.value)}
              minLength={3} maxLength={32} required autoFocus
            />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input
              type="password" className="form-control"
              value={password} onChange={(e) => setPassword(e.target.value)}
              minLength={6} maxLength={72} required
            />
          </div>
          <div className="form-group">
            <label>Confirm password</label>
            <input
              type="password" className="form-control"
              value={confirm} onChange={(e) => setConfirm(e.target.value)}
              minLength={6} maxLength={72} required
            />
          </div>

          <button
            type="submit" className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem' }}
            disabled={submitting}
          >
            <UserPlus size={16} style={{ marginRight: 8 }} />
            {submitting ? 'Submitting…' : 'Submit request'}
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

          <div style={{
            marginTop: '1rem', paddingTop: '1rem',
            borderTop: '1px solid var(--border-color)',
            fontSize: '0.8rem', color: 'var(--text-secondary)',
          }}>
            Already have an account? <Link to="/login" style={{ color: 'var(--primary-color)' }}>Sign in</Link>
          </div>
        </form>
      </div>
    </div>
  );
}
