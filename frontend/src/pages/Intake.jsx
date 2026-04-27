import React, { useState, useEffect, useRef } from 'react';
import axios from 'axios';
import { UploadCloud, CheckCircle2, AlertCircle, Loader2, Clock, Zap } from 'lucide-react';

const API = 'http://localhost:8000';
const POLL_INTERVAL_MS = 2500;

// ── Task status badge helper ──────────────────────────────────────────────────
const STATUS_META = {
  QUEUED:    { label: 'Queued',    color: '#94a3b8', icon: Clock  },
  RUNNING:   { label: 'Processing…', color: '#f59e0b', icon: Loader2 },
  COMPLETED: { label: 'Completed', color: '#10b981', icon: CheckCircle2 },
  FAILED:    { label: 'Failed',    color: '#ef4444', icon: AlertCircle  },
};

export default function Intake() {
  const [file, setFile]               = useState(null);
  const [submitting, setSubmitting]   = useState(false);
  const [taskId, setTaskId]           = useState(null);
  const [taskStatus, setTaskStatus]   = useState(null); // QUEUED | RUNNING | COMPLETED | FAILED
  const [taskResult, setTaskResult]   = useState(null);
  const [error, setError]             = useState(null);
  const pollRef                       = useRef(null);

  const [formData, setFormData] = useState({
    customer_id:   'C001',
    change_type:   'LEGAL_NAME_CHANGE',
    old_value:     'Priya Sharma',
    new_value:     'Priya Mehta',
    document_type: 'MARRIAGE_CERTIFICATE',
  });

  // ── Cleanup poll timer on unmount ─────────────────────────────────────────
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  // ── Start polling when a task_id is received ──────────────────────────────
  useEffect(() => {
    if (!taskId) return;
    if (pollRef.current) clearInterval(pollRef.current);

    const poll = async () => {
      try {
        const { data } = await axios.get(`${API}/api/tasks/${taskId}`);
        setTaskStatus(data.status);

        if (data.status === 'COMPLETED') {
          setTaskResult(data.result);
          clearInterval(pollRef.current);
          setSubmitting(false);
        } else if (data.status === 'FAILED') {
          setError(data.error || 'Pipeline task failed. Check backend logs.');
          clearInterval(pollRef.current);
          setSubmitting(false);
        }
      } catch (e) {
        setError('Lost connection while polling task status.');
        clearInterval(pollRef.current);
        setSubmitting(false);
      }
    };

    // Poll immediately, then on interval
    poll();
    pollRef.current = setInterval(poll, POLL_INTERVAL_MS);
  }, [taskId]);

  const handleFileChange = (e) => {
    if (e.target.files?.[0]) setFile(e.target.files[0]);
  };

  const handleInputChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const resetState = () => {
    setTaskId(null);
    setTaskStatus(null);
    setTaskResult(null);
    setError(null);
    setFile(null);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) { setError('Please select a document to upload'); return; }

    setSubmitting(true);
    setError(null);
    setTaskId(null);
    setTaskStatus(null);
    setTaskResult(null);

    const fd = new FormData();
    fd.append('customer_id',   formData.customer_id);
    fd.append('change_type',   formData.change_type);
    fd.append('old_value',     formData.old_value);
    fd.append('new_value',     formData.new_value);
    fd.append('document_type', formData.document_type);
    fd.append('document',      file);

    try {
      // Backend now returns 202 Accepted with task_id
      const { data } = await axios.post(`${API}/api/intake`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });

      if (data.task_id) {
        setTaskId(data.task_id);
        setTaskStatus('QUEUED');
        // submitting stays true — polling will clear it
      } else if (data.status === 'VALIDATION_FAILED') {
        setError(data.message);
        setSubmitting(false);
      } else {
        // Fallback: old synchronous response shape
        setTaskResult(data);
        setTaskStatus('COMPLETED');
        setSubmitting(false);
      }
    } catch (err) {
      setError(err.response?.data?.detail || 'An error occurred during submission');
      setSubmitting(false);
    }
  };

  // ── Render helpers ────────────────────────────────────────────────────────
  const meta = taskStatus ? STATUS_META[taskStatus] : null;
  const StatusIcon = meta?.icon;

  const isSpinning = taskStatus === 'RUNNING' || taskStatus === 'QUEUED';
  const requestId  = taskResult?.request_id || taskResult?.final_status;

  return (
    <div>
      <div style={{ marginBottom: '2rem' }}>
        <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Staff Intake Form</h2>
        <p style={{ color: 'var(--text-secondary)' }}>
          Submit a change request. The AI pipeline runs asynchronously — status updates in real-time below.
        </p>
      </div>

      <div className="grid-2">
        {/* ── Left: Form fields ─────────────────────────────────────────── */}
        <div>
          <div className="card">
            <h3 style={{ marginBottom: '1rem', fontSize: '1.1rem' }}>Request Details</h3>

            <div className="form-group">
              <label>Customer ID</label>
              <input
                type="text" className="form-control"
                name="customer_id" value={formData.customer_id}
                onChange={handleInputChange} disabled={submitting}
              />
            </div>

            <div className="form-group">
              <label>Change Type</label>
              <input type="text" className="form-control" value="Legal Name Change" readOnly />
            </div>

            <div className="form-group">
              <label>Old Name (Current in System)</label>
              <input
                type="text" className="form-control"
                name="old_value" value={formData.old_value}
                onChange={handleInputChange} disabled={submitting}
              />
            </div>

            <div className="form-group">
              <label>New Name (Requested)</label>
              <input
                type="text" className="form-control"
                name="new_value" value={formData.new_value}
                onChange={handleInputChange} disabled={submitting}
              />
            </div>

            <div className="form-group">
              <label>Document Type</label>
              <select
                className="form-control"
                name="document_type" value={formData.document_type}
                onChange={handleInputChange} disabled={submitting}
              >
                <option value="MARRIAGE_CERTIFICATE">Marriage Certificate</option>
                <option value="GAZETTE_NOTIFICATION">Gazette Notification</option>
                <option value="DEED_POLL">Deed Poll</option>
              </select>
            </div>
          </div>
        </div>

        {/* ── Right: Upload + status ────────────────────────────────────── */}
        <div>
          <div className="card">
            <h3 style={{ marginBottom: '1rem', fontSize: '1.1rem' }}>Upload Document</h3>

            <label className="file-upload-zone" style={{ display: 'block', opacity: submitting ? 0.5 : 1, pointerEvents: submitting ? 'none' : 'auto' }}>
              <input
                type="file"
                style={{ display: 'none' }}
                onChange={handleFileChange}
                accept=".pdf,.png,.jpg,.jpeg"
                disabled={submitting}
              />
              <UploadCloud size={48} color="var(--primary-color)" style={{ margin: '0 auto 1rem auto' }} />
              <p style={{ fontWeight: 500, marginBottom: '0.5rem' }}>
                {file ? file.name : 'Click to select or drag and drop'}
              </p>
              <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>
                PDF, JPG, PNG up to 20MB
              </p>
            </label>

            <button
              className="btn btn-primary"
              style={{ width: '100%', marginTop: '1.5rem' }}
              onClick={handleSubmit}
              disabled={submitting || !file}
              id="intake-submit-btn"
            >
              {submitting
                ? <><Loader2 size={16} style={{ marginRight: 8, animation: 'spin 1s linear infinite' }} />Sending to Agent Pipeline…</>
                : <><Zap size={16} style={{ marginRight: 8 }} />Submit to AI Document Processor</>
              }
            </button>

            {/* ── Live Task Status Panel ───────────────────────────── */}
            {taskId && meta && (
              <div style={{
                marginTop: '1.25rem',
                padding: '1rem',
                borderRadius: '0.5rem',
                border: `1px solid ${meta.color}44`,
                background: `${meta.color}11`,
              }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
                  <StatusIcon
                    size={20}
                    color={meta.color}
                    style={isSpinning ? { animation: 'spin 1.2s linear infinite' } : {}}
                  />
                  <span style={{ fontWeight: 600, color: meta.color }}>{meta.label}</span>
                </div>

                {/* Progress steps */}
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.8 }}>
                  {['QUEUED', 'RUNNING', 'COMPLETED'].map((step, i) => {
                    const steps = ['QUEUED', 'RUNNING', 'COMPLETED', 'FAILED'];
                    const currentIdx = steps.indexOf(taskStatus);
                    const stepIdx    = steps.indexOf(step);
                    const done = currentIdx > stepIdx || taskStatus === step;
                    const active = taskStatus === step;
                    return (
                      <div key={step} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                        <span style={{
                          width: 8, height: 8, borderRadius: '50%',
                          background: done ? meta.color : 'var(--border-color)',
                          display: 'inline-block', flexShrink: 0,
                          boxShadow: active ? `0 0 6px ${meta.color}` : 'none',
                        }} />
                        <span style={{ color: done ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
                          {step === 'QUEUED'    && 'Request queued for AI pipeline'}
                          {step === 'RUNNING'   && 'Gemini extracting & scoring document…'}
                          {step === 'COMPLETED' && 'Pipeline finished — result ready'}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* Task ID */}
                <div style={{ marginTop: '0.75rem', fontSize: '0.72rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                  Task ID: {taskId}
                </div>

                {/* On COMPLETED — show outcome based on final_status */}
                {taskStatus === 'COMPLETED' && taskResult && (() => {
                  const finalStatus = taskResult.final_status;
                  const isValidationFail = finalStatus === 'VALIDATION_FAILED';
                  const isPending = finalStatus === 'AI_VERIFIED_PENDING_HUMAN';

                  return (
                    <div style={{ marginTop: '1rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-color)' }}>

                      {/* VALIDATION FAILED — wrong customer ID or old value mismatch */}
                      {isValidationFail && (
                        <>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                            <AlertCircle size={16} color="#ef4444" />
                            <span style={{ fontWeight: 600, color: '#ef4444', fontSize: '0.9rem' }}>
                              Validation Failed — Request Rejected
                            </span>
                          </div>
                          <div style={{
                            background: 'rgba(239,68,68,0.1)',
                            border: '1px solid rgba(239,68,68,0.3)',
                            borderRadius: '0.375rem',
                            padding: '0.625rem 0.75rem',
                            fontSize: '0.8rem',
                            color: '#fca5a5',
                            lineHeight: 1.6,
                          }}>
                            {taskResult.error ||
                              'The submitted details do not match the records in our system. ' +
                              'Please verify the Customer ID and the current value on record, then re-submit.'}
                          </div>
                          <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                            ℹ️ This request has <strong>not</strong> been added to the Checker queue.
                            Correct the details and re-submit a new request.
                          </div>
                        </>
                      )}

                      {/* SUCCESS — moved to checker queue */}
                      {isPending && (
                        <>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
                            <CheckCircle2 size={16} color="#10b981" />
                            <span style={{ fontWeight: 600, color: '#10b981', fontSize: '0.9rem' }}>
                              Moved to Checker Queue
                            </span>
                          </div>
                          {taskResult.request_id && (
                            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                              Request ID: {taskResult.request_id}
                            </p>
                          )}
                        </>
                      )}

                      {/* Unknown final status */}
                      {!isValidationFail && !isPending && (
                        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                          Pipeline status: <code>{finalStatus || 'unknown'}</code>
                        </div>
                      )}

                      <button
                        className="btn"
                        style={{ marginTop: '0.75rem', fontSize: '0.8rem', padding: '0.4rem 0.8rem', background: 'rgba(255,255,255,0.1)', color: 'white' }}
                        onClick={resetState}
                      >
                        Submit another request
                      </button>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* ── Error ───────────────────────────────────────────────── */}
            {error && (
              <div style={{ marginTop: '1rem', padding: '1rem', background: 'rgba(239, 68, 68, 0.1)', color: 'var(--danger-color)', borderRadius: '0.5rem', display: 'flex', alignItems: 'flex-start', gap: '0.5rem' }}>
                <AlertCircle size={20} style={{ flexShrink: 0 }} />
                <span style={{ fontSize: '0.875rem' }}>{error}</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
