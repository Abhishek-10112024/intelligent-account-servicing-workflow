/**
 * ErrorBoundary — last-resort render guard.
 *
 * Without this, any unhandled error inside the React tree renders a blank
 * screen. With it, you get a readable error message that says what broke
 * and a stack trace in the console.
 */
import React from 'react';

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary] caught render error:', error, info);
    this.setState({ info });
  }

  handleReset = () => {
    this.setState({ error: null, info: null });
    // Go back to root. Avoids leaving the app stuck on the failing route.
    if (window.location.pathname !== '/') {
      window.location.href = '/';
    }
  };

  handleClearSession = () => {
    try {
      localStorage.removeItem('iasw_token');
      localStorage.removeItem('iasw_user');
    } catch { /* empty */ }
    window.location.href = '/login';
  };

  render() {
    if (!this.state.error) return this.props.children;

    const msg = this.state.error.message || String(this.state.error);
    const stack = this.state.error.stack || '';

    return (
      <div style={{ padding: '2rem', maxWidth: 720, margin: '2rem auto', color: '#f8fafc', fontFamily: 'Inter, sans-serif' }}>
        <div style={{
          background: 'rgba(30,41,59,0.85)',
          border: '1px solid rgba(239,68,68,0.4)',
          borderRadius: '0.75rem',
          padding: '1.5rem',
        }}>
          <h2 style={{ color: '#ef4444', marginBottom: '0.5rem' }}>Something went wrong</h2>
          <p style={{ color: '#94a3b8', marginBottom: '1rem' }}>
            The UI hit an error and couldn't render. Detail below.
          </p>
          <pre style={{
            whiteSpace: 'pre-wrap',
            background: '#0f172a',
            padding: '1rem',
            borderRadius: '0.5rem',
            fontSize: '0.8rem',
            color: '#fca5a5',
            overflow: 'auto',
            maxHeight: 300,
          }}>
{msg}
{'\n\n'}
{stack}
          </pre>
          <div style={{ marginTop: '1rem', display: 'flex', gap: '0.5rem' }}>
            <button onClick={this.handleReset} style={{
              padding: '0.5rem 1rem', borderRadius: '0.5rem',
              background: '#4f46e5', color: 'white', border: 'none', cursor: 'pointer',
            }}>
              Back to home
            </button>
            <button onClick={this.handleClearSession} style={{
              padding: '0.5rem 1rem', borderRadius: '0.5rem',
              background: 'rgba(255,255,255,0.1)', color: 'white', border: '1px solid rgba(255,255,255,0.2)', cursor: 'pointer',
            }}>
              Clear session &amp; re-login
            </button>
          </div>
        </div>
      </div>
    );
  }
}
