import React from 'react';
import { BrowserRouter, Routes, Route, Link, useLocation } from 'react-router-dom';
import Intake from './pages/Intake';
import Checker from './pages/Checker';
import { ShieldCheck, FileInput } from 'lucide-react';

function Navigation() {
  const location = useLocation();
  
  return (
    <nav className="nav-links">
      <Link 
        to="/" 
        className={`nav-link ${location.pathname === '/' ? 'active' : ''}`}
        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
      >
        <FileInput size={18} />
        Staff Intake
      </Link>
      <Link 
        to="/checker" 
        className={`nav-link ${location.pathname === '/checker' ? 'active' : ''}`}
        style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}
      >
        <ShieldCheck size={18} />
        Checker Queue
      </Link>
    </nav>
  );
}

function App() {
  return (
    <BrowserRouter>
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
              <Route path="/" element={<Intake />} />
              <Route path="/checker" element={<Checker />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  );
}

export default App;
