import React from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation, Navigate } from 'react-router-dom';
import Intake from './pages/Intake';
import Checker from './pages/Checker';
import Login from './pages/Login';
import Register from './pages/Register';
import Registrations from './pages/Registrations';
import ProtectedRoute from './auth/ProtectedRoute';
import { AuthProvider, useAuth } from './auth/AuthContext';
import { ShieldCheck, FileInput, LogOut, Users } from 'lucide-react';

function Navigation() {
  const location = useLocation();
  const { user, isAdmin, logout } = useAuth();

  if (!user) return null;

  return (
    <nav className="nav-links" style={{ alignItems: 'center' }}>
      {/* USER role: only sees intake. ADMIN role: sees both. */}
      {!isAdmin && (
        <Link
          to="/"
          className={`nav-link ${location.pathname === '/' ? 'active' : ''}`}
          style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
        >
          <FileInput size={18} />
          Staff Intake
        </Link>
      )}
      {isAdmin && (
        <>
          <Link
            to="/checker"
            className={`nav-link ${location.pathname === '/checker' ? 'active' : ''}`}
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
          >
            <ShieldCheck size={18} />
            Checker Queue
          </Link>
          <Link
            to="/registrations"
            className={`nav-link ${location.pathname === '/registrations' ? 'active' : ''}`}
            style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
          >
            <Users size={18} />
            Registrations
          </Link>
        </>
      )}

      {/* User chip + logout */}
      <div style={{
        marginLeft: '0.75rem', paddingLeft: '0.75rem',
        borderLeft: '1px solid var(--border-color)',
        display: 'flex', alignItems: 'center', gap: '0.75rem',
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end' }}>
          <span style={{ fontSize: '0.85rem', fontWeight: 500 }}>{user.username}</span>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>{user.role}</span>
        </div>
        <button
          onClick={logout}
          className="btn"
          style={{
            background: 'rgba(255,255,255,0.08)', color: 'white',
            padding: '0.4rem 0.75rem', fontSize: '0.8rem',
          }}
          title="Sign out"
        >
          <LogOut size={14} /> Logout
        </button>
      </div>
    </nav>
  );
}

// Redirects the "/" entry point based on role, so admins don't land on the
// intake form they can't use.
function HomeRedirect() {
  const { user, isAdmin } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  return isAdmin ? <Navigate to="/checker" replace /> : <Intake />;
}

function Shell() {
  return (
    <div className="app-container">
      <div className="glass-panel">
        <header className="header">
          <div>
            <h1>IASW Prototype</h1>
            <p style={{ color: 'var(--text-secondary)', marginTop: '0.25rem', fontSize: '0.875rem' }}>
              Intelligent Account Servicing Workflow - Legal Name Change
            </p>
          </div>
          <Navigation />
        </header>

        <main>
          <Routes>
            <Route path="/login"    element={<Login />} />
            <Route path="/register" element={<Register />} />

            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <HomeRedirect />
                </ProtectedRoute>
              }
            />
            <Route
              path="/checker"
              element={
                <ProtectedRoute role="ADMIN">
                  <Checker />
                </ProtectedRoute>
              }
            />
            <Route
              path="/registrations"
              element={
                <ProtectedRoute role="ADMIN">
                  <Registrations />
                </ProtectedRoute>
              }
            />

            {/* Anything else → home (which will punt to /login if unauth'd). */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Shell />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
