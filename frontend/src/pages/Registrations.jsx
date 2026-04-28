/**
 * Registrations — admin-only panel to approve or reject pending user registrations.
 * Mounted on its own route at /registrations.
 */
import React, { useEffect, useState } from 'react';
import { Users, CheckCircle2, XCircle, RefreshCw } from 'lucide-react';
import api from '../api';

export default function Registrations() {
  const [items, setItems]       = useState([]);
  const [loading, setLoading]   = useState(true);
  const [acting, setActing]     = useState(null);   // registration id currently being decided
  const [error, setError]       = useState(null);

  const fetchItems = async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/api/auth/registrations?status_filter=PENDING');
      setItems(data);
      setError(null);
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load registrations.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchItems(); }, []);

  const decide = async (registrationId, decision) => {
    setActing(registrationId);
    try {
      await api.post('/api/auth/registrations/decide', {
        registration_id: registrationId,
        decision,
        notes: decision === 'APPROVED' ? 'Approved by admin.' : 'Rejected by admin.',
      });
      await fetchItems();
    } catch (err) {
      setError(err?.response?.data?.detail || 'Decision failed.');
    } finally {
      setActing(null);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div>
          <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Pending User Registrations</h2>
          <p style={{ color: 'var(--text-secondary)' }}>
            Approve or reject new user account requests.
          </p>
        </div>
        <button
          className="btn" style={{ background: 'rgba(255,255,255,0.1)', color: 'white' }}
          onClick={fetchItems}
        >
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {error && (
        <div style={{
          marginBottom: '1rem', padding: '0.75rem',
          background: 'rgba(239,68,68,0.1)', color: 'var(--danger-color)',
          borderRadius: '0.5rem', fontSize: '0.875rem',
        }}>
          {error}
        </div>
      )}

      {loading ? (
        <div className="empty-state">Loading…</div>
      ) : items.length === 0 ? (
        <div className="card empty-state">
          <Users size={40} style={{ margin: '0 auto 0.75rem auto', color: 'var(--text-secondary)', opacity: 0.5 }} />
          <h3>No pending registrations</h3>
          <p style={{ marginTop: '0.5rem' }}>New requests will show up here.</p>
        </div>
      ) : (
        <div style={{ background: 'rgba(30, 41, 59, 0.4)', borderRadius: '0.75rem', overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
            <thead>
              <tr style={{ background: 'rgba(255,255,255,0.05)', textAlign: 'left' }}>
                <th style={{ padding: '1rem' }}>Username</th>
                <th style={{ padding: '1rem' }}>Requested role</th>
                <th style={{ padding: '1rem' }}>Submitted</th>
                <th style={{ padding: '1rem', textAlign: 'right' }}>Action</th>
              </tr>
            </thead>
            <tbody>
              {items.map((r) => (
                <tr key={r.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                  <td style={{ padding: '1rem', fontWeight: 500 }}>{r.username}</td>
                  <td style={{ padding: '1rem' }}>{r.requested_role}</td>
                  <td style={{ padding: '1rem', color: 'var(--text-secondary)' }}>
                    {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                  </td>
                  <td style={{ padding: '1rem', textAlign: 'right' }}>
                    <button
                      className="btn btn-danger" style={{ marginRight: '0.5rem', padding: '0.4rem 0.75rem', fontSize: '0.8rem' }}
                      onClick={() => decide(r.id, 'REJECTED')}
                      disabled={acting === r.id}
                    >
                      <XCircle size={14} /> Reject
                    </button>
                    <button
                      className="btn btn-success" style={{ padding: '0.4rem 0.75rem', fontSize: '0.8rem' }}
                      onClick={() => decide(r.id, 'APPROVED')}
                      disabled={acting === r.id}
                    >
                      <CheckCircle2 size={14} /> Approve
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
