import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { ShieldCheck, User, Search, RefreshCw, CheckCircle2, XCircle } from 'lucide-react';

export default function Checker() {
  const [queue, setQueue] = useState([]);
  const [historyQueue, setHistoryQueue] = useState([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState(null);
  
  // Simulated Checker ID for the demo
  const checkerId = "checker_sup_01";

  const fetchQueue = async () => {
    setLoading(true);
    try {
      const response = await axios.get('http://localhost:8000/api/checker/queue');
      const historyResponse = await axios.get('http://localhost:8000/api/checker/history');
      setQueue(response.data);
      setHistoryQueue(historyResponse.data);
      setError(null);
    } catch (err) {
      setError(err.response?.data?.detail || "Failed to fetch checker queue.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchQueue();
  }, []);

  const handleDecision = async (requestId, decision) => {
    setActionLoading(true);
    try {
      await axios.post('http://localhost:8000/api/checker/decide', {
        request_id: requestId,
        checker_id: checkerId,
        decision: decision,
        notes: decision === 'APPROVED' ? "All documents verified by checker." : "Rejected by checker."
      });
      // Refresh the queue
      await fetchQueue();
    } catch (err) {
      setError(err.response?.data?.detail || `Failed to process decision: ${decision}`);
    } finally {
      setActionLoading(false);
    }
  };

  if (loading) {
    return <div className="empty-state"><RefreshCw className="animate-spin" style={{ margin: '0 auto', animation: 'spin 2s linear infinite' }} /> Loading Queue...</div>;
  }

  const pendingItems = queue.filter(item => item.overall_status === 'AI_VERIFIED_PENDING_HUMAN');

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <div>
          <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Human-in-the-Loop: Checker Queue</h2>
          <p style={{ color: 'var(--text-secondary)' }}>
            Review AI confidence scores and approve or reject the requested changes.
          </p>
        </div>
        <button className="btn" style={{ background: 'rgba(255,255,255,0.1)', color: 'white' }} onClick={fetchQueue}>
          <RefreshCw size={16} /> Refresh
        </button>
      </div>

      {error && (
        <div style={{ marginBottom: '1.5rem', padding: '1rem', background: 'rgba(239, 68, 68, 0.1)', color: 'var(--danger-color)', borderRadius: '0.5rem' }}>
          {error}
        </div>
      )}

      {pendingItems.length === 0 ? (
        <div className="card empty-state">
          <ShieldCheck size={48} style={{ margin: '0 auto 1rem auto', color: 'var(--text-secondary)', opacity: 0.5 }} />
          <h3>All caught up!</h3>
          <p style={{ marginTop: '0.5rem' }}>There are no pending requests requiring human review.</p>
        </div>
      ) : (
        <div className="grid-2">
          {pendingItems.map((item) => {
            const overall_confidence = (item.confidence_name * 0.6) + (item.confidence_authenticity * 0.4) || 0;
            return (
            <div key={item.id} className="card" style={{ display: 'flex', flexDirection: 'column' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1rem' }}>
                <div>
                  <span className="status-badge status-pending">
                    {item.overall_status.replace(/_/g, ' ')}
                  </span>
                  <h3 style={{ marginTop: '0.75rem', fontSize: '1.1rem' }}>{item.change_type.replace(/_/g, ' ')}</h3>
                  <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginTop: '0.25rem' }}>
                    Customer ID: {item.customer_id}
                  </p>
                </div>
                <div style={{ textAlign: 'right' }}>
                  <div style={{ fontSize: '1.5rem', fontWeight: 700, color: overall_confidence >= 0.8 ? 'var(--success-color)' : 'var(--warning-color)' }}>
                    {(overall_confidence * 100).toFixed(0)}%
                  </div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>AI Confidence</div>
                </div>
              </div>

              <div style={{ background: 'rgba(15,23,42,0.5)', padding: '1rem', borderRadius: '0.5rem', marginBottom: '1rem' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>Requested Change</div>
                    <div style={{ fontWeight: 500 }}><span style={{ textDecoration: 'line-through', color: 'var(--danger-color)', marginRight: '0.5rem' }}>{item.old_value}</span> {item.new_value}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.25rem' }}>AI Extracted Target</div>
                    <div style={{ fontWeight: 500, color: 'var(--primary-color)' }}>{item.extracted_value || item.new_value}</div>
                  </div>
                </div>
              </div>

              <div className="ai-summary-box" style={{ flexGrow: 1 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', fontWeight: 600 }}>
                  <Search size={16} color="var(--success-color)" />
                  AI Verification Summary
                </div>
                <div style={{ fontSize: '0.875rem', lineHeight: 1.5, whiteSpace: 'pre-line' }}>
                  {item.ai_summary}
                </div>
              </div>

              {/* Document Preview */}
              <div style={{ marginTop: '1rem' }}>
                 <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>Uploaded Document ({item.document_type || 'Unknown'})</div>
                 <iframe 
                   src={`http://localhost:8000/api/checker/document/${item.id}`} 
                   style={{ width: '100%', height: '250px', border: '1px solid var(--border-color)', borderRadius: '0.5rem', backgroundColor: '#fff' }} 
                   title="Document Preview" 
                 />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginTop: '1rem' }}>
                <button 
                  className="btn btn-danger" 
                  onClick={() => handleDecision(item.id, 'REJECTED')}
                  disabled={actionLoading}
                >
                  <XCircle size={18} /> Reject
                </button>
                <button 
                  className="btn btn-success" 
                  onClick={() => handleDecision(item.id, 'APPROVED')}
                  disabled={actionLoading}
                >
                  <CheckCircle2 size={18} /> Approve
                </button>
              </div>
            </div>
          )})}
        </div>
      )}
      
      {/* Show Recently processed (for demo visibility) */}
      {historyQueue.length > 0 && (
         <div style={{ marginTop: '3rem' }}>
            <h3 style={{ fontSize: '1.1rem', marginBottom: '1rem', color: 'var(--text-secondary)' }}>Recently Processed History</h3>
            <div style={{ background: 'rgba(30, 41, 59, 0.4)', borderRadius: '0.75rem', overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.05)', textAlign: 'left' }}>
                    <th style={{ padding: '1rem' }}>ID</th>
                    <th style={{ padding: '1rem' }}>Customer</th>
                    <th style={{ padding: '1rem' }}>Change</th>
                    <th style={{ padding: '1rem' }}>Checker</th>
                    <th style={{ padding: '1rem' }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {historyQueue.map(item => (
                    <tr key={item.id} style={{ borderTop: '1px solid var(--border-color)' }}>
                      <td style={{ padding: '1rem', color: 'var(--text-secondary)' }}>{item.id.substring(0,8)}...</td>
                      <td style={{ padding: '1rem' }}>{item.customer_id}</td>
                      <td style={{ padding: '1rem' }}>{item.old_value} → {item.new_value}</td>
                      <td style={{ padding: '1rem' }}>{item.checker_id}</td>
                      <td style={{ padding: '1rem' }}>
                        <span className={`status-badge ${item.overall_status === 'APPROVED' ? 'status-approved' : 'status-rejected'}`}>
                          {item.overall_status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
         </div>
      )}
    </div>
  );
}
