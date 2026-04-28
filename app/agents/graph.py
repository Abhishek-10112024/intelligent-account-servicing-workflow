"""
graph.py — LangGraph Agentic Workflow Definition.

This module defines the IASW state machine using LangGraph.

Workflow graph:
  START
    │
    ▼
  validate_request        ← Agent 1: RPS cross-reference check
    │
    ├─ FAIL ──────────────→ END (validation_error state)
    │
    ▼
  process_document        ← Agent 2: OCR + LLM extraction + FileNet archive
    │
    ▼
  score_confidence        ← Agent 3: Confidence Score Card generation
    │
    ▼
  stage_to_pending        ← Write to Pending Table, status = AI_VERIFIED_PENDING_HUMAN
    │
    ▼
  END (graph pauses — HITL boundary)
  ════════════════════════════════════
  [Human Checker reviews and POSTs decision via /checker/decide]
  ════════════════════════════════════
    │
    ▼ (resumed by checker router)
  [Mock RPS write executes in /rps/write]
    │
    ▼
  END

Note on HITL enforcement:
  LangGraph's interrupt() mechanism is used conceptually here.  Because this
  prototype uses a stateless HTTP API (FastAPI), the HITL pause is implemented
  as a DB-level status gate rather than a serialised graph state:
    - After stage_to_pending, the graph finishes (returns request_id).
    - The /checker/decide endpoint is the "resume" point.
    - The /rps/write endpoint has a hard DB check: status must be
      AI_VERIFIED_PENDING_HUMAN and checker_decision must be APPROVED.
  This is equivalent to LangGraph's interrupt() pattern in a stateless HTTP context.
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional, Any

from langgraph.graph import StateGraph, END

from app.agents.validation_agent   import run_validation
from app.agents.document_processor import process_document
from app.agents.confidence_scorer  import compute_confidence
from app.services.observability    import log_agent_step
from app.database                  import PendingRequest, SessionLocal
from app.config                    import settings


# ── State definition ──────────────────────────────────────────────────────────

class WorkflowState(TypedDict):
    """
    Immutable-ish state dict passed between LangGraph nodes.
    Each node returns a partial update; LangGraph merges it.
    """
    # Inputs (set at graph invocation)
    request_id:    str
    customer_id:   str
    change_type:   str
    old_value:     str
    new_value:     str
    document_type: str
    file_path:     str          # Absolute path to uploaded temp file

    # Populated by validate_request
    validation_valid:         Optional[bool]
    validation_error:         Optional[str]
    rps_current_value:        Optional[str]

    # Populated by process_document
    extracted_fields:         Optional[dict]
    forgery_check:            Optional[str]
    forgery_signals:          Optional[list]
    forgery_reasoning:        Optional[str]
    filenet_ref_id:           Optional[str]
    processing_mode:          Optional[str]   # "real" or "mock"

    # Populated by score_confidence
    score_card:               Optional[dict]

    # Populated by stage_to_pending
    staged:                   Optional[bool]
    final_status:             Optional[str]   # AI_VERIFIED_PENDING_HUMAN | VALIDATION_FAILED
    error_message:            Optional[str]

    # DB session handle (injected at start, not serialisable — used internally only)
    _db:                      Optional[Any]


# ── Node 1: Validate Request ──────────────────────────────────────────────────

def validate_request_node(state: WorkflowState) -> dict:
    """
    Agent 1: Validate customer and old_value against mock RPS.
    On failure, sets final_status = VALIDATION_FAILED and halts routing.
    """
    db = state["_db"]
    result = run_validation(
        customer_id=state["customer_id"],
        change_type=state["change_type"],
        old_value=state["old_value"],
        db=db,
    )

    log_agent_step(
        db=db,
        request_id=state["request_id"],
        actor="validation_agent",
        action="VALIDATION_RESULT",
        detail=result.to_dict(),
    )

    return {
        "validation_valid":  result.valid,
        "validation_error":  result.error,
        "rps_current_value": result.rps_current_value,
    }


def _route_after_validation(state: WorkflowState) -> str:
    """Conditional edge: proceed to document processing or abort."""
    return "process_document" if state["validation_valid"] else "handle_validation_error"


# ── Node: Handle Validation Error ─────────────────────────────────────────────

def handle_validation_error_node(state: WorkflowState) -> dict:
    """Write a VALIDATION_FAILED record to the pending table and end the graph."""
    db = state["_db"]
    record = PendingRequest(
        id=state["request_id"],
        change_type=state["change_type"],
        customer_id=state["customer_id"],
        old_value=state["old_value"],
        new_value=state["new_value"],
        overall_status="VALIDATION_FAILED",
        ai_summary=state.get("validation_error", "Validation failed."),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(record)
    db.commit()

    log_agent_step(
        db=db,
        request_id=state["request_id"],
        actor="validation_agent",
        action="VALIDATION_FAILED_STAGED",
        detail={"error": state.get("validation_error")},
    )

    return {
        "staged": True,
        "final_status": "VALIDATION_FAILED",
        "error_message": state.get("validation_error"),
    }


# ── Node 2: Process Document ──────────────────────────────────────────────────

def process_document_node(state: WorkflowState) -> dict:
    """Agent 2: Run OCR extraction, forgery checks, archive to FileNet."""
    db = state["_db"]
    result = process_document(
        file_path=Path(state["file_path"]),
        customer_id=state["customer_id"],
        change_type=state["change_type"],
        document_type=state["document_type"],
        request_id=state["request_id"],
    )

    log_agent_step(
        db=db,
        request_id=state["request_id"],
        actor="document_processor",
        action="DOCUMENT_PROCESSED",
        detail={
            "forgery_check":     result["forgery_check"],
            "forgery_signals":   result.get("forgery_signals", []),
            "forgery_reasoning": result.get("forgery_reasoning", ""),
            "filenet_ref_id":    result["filenet_ref_id"],
            "mode":              result["mode"],
            "fields_extracted":  list(result["extracted_fields"].keys()),
        },
    )

    return {
        "extracted_fields":  result["extracted_fields"],
        "forgery_check":     result["forgery_check"],
        "forgery_signals":   result.get("forgery_signals", []),
        "forgery_reasoning": result.get("forgery_reasoning", ""),
        "filenet_ref_id":    result["filenet_ref_id"],
        "processing_mode":   result["mode"],
    }


# ── Node 3: Score Confidence ──────────────────────────────────────────────────

def score_confidence_node(state: WorkflowState) -> dict:
    """Agent 3: Compute per-field and overall confidence scores."""
    db = state["_db"]
    score_card = compute_confidence(
        extracted_fields=state["extracted_fields"],
        change_type=state["change_type"],
        document_type=state["document_type"],
        old_value=state["old_value"],
        new_value=state["new_value"],
        forgery_check=state["forgery_check"],
        forgery_signals=state.get("forgery_signals") or [],
        forgery_reasoning=state.get("forgery_reasoning") or "",
    )

    log_agent_step(
        db=db,
        request_id=state["request_id"],
        actor="confidence_scorer",
        action="CONFIDENCE_SCORED",
        detail={
            "name_match":         score_card["name_match"],
            "authenticity":       score_card["authenticity"],
            "overall_confidence": score_card["overall_confidence"],
            "recommendation":     score_card["recommendation"],
        },
    )

    return {"score_card": score_card}


# ── Node 4: Stage to Pending Table ────────────────────────────────────────────

def stage_to_pending_node(state: WorkflowState) -> dict:
    """
    Write the fully-verified request to the Pending Table.
    Status is set to AI_VERIFIED_PENDING_HUMAN — the HITL boundary.

    After this node, the graph terminates. The Checker must explicitly
    POST to /checker/decide to advance the status to APPROVED or REJECTED.
    """
    db    = state["_db"]
    score = state["score_card"]

    record = PendingRequest(
        id=state["request_id"],
        change_type=state["change_type"],
        customer_id=state["customer_id"],
        old_value=state["old_value"],
        new_value=state["new_value"],
        extracted_value=score.get("extracted_name", ""),
        document_type=state["document_type"],
        filenet_ref_id=state["filenet_ref_id"],
        confidence_name=score["name_match"],
        confidence_authenticity=score["authenticity"],
        forgery_check=score["forgery_check"],
        ai_summary=score["summary"],
        ai_recommendation=score["recommendation"],
        overall_status="AI_VERIFIED_PENDING_HUMAN",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(record)
    db.commit()

    log_agent_step(
        db=db,
        request_id=state["request_id"],
        actor="iasw_graph",
        action="STAGED_PENDING_HUMAN",
        detail={
            "overall_status":    "AI_VERIFIED_PENDING_HUMAN",
            "overall_confidence": score["overall_confidence"],
            "recommendation":     score["recommendation"],
        },
    )

    return {
        "staged":       True,
        "final_status": "AI_VERIFIED_PENDING_HUMAN",
    }


# ── Build Graph ───────────────────────────────────────────────────────────────

def build_iasw_graph():
    """Construct and compile the IASW LangGraph state machine."""
    g = StateGraph(WorkflowState)

    g.add_node("validate_request",        validate_request_node)
    g.add_node("handle_validation_error", handle_validation_error_node)
    g.add_node("process_document",        process_document_node)
    g.add_node("score_confidence",        score_confidence_node)
    g.add_node("stage_to_pending",        stage_to_pending_node)

    g.set_entry_point("validate_request")

    g.add_conditional_edges(
        "validate_request",
        _route_after_validation,
        {
            "process_document":        "process_document",
            "handle_validation_error": "handle_validation_error",
        },
    )

    g.add_edge("handle_validation_error", END)
    g.add_edge("process_document",        "score_confidence")
    g.add_edge("score_confidence",        "stage_to_pending")
    g.add_edge("stage_to_pending",        END)

    return g.compile()


# Module-level compiled graph — imported by routers
iasw_graph = build_iasw_graph()


# ── Graph Runner ──────────────────────────────────────────────────────────────

def run_iasw_pipeline(
    customer_id:   str,
    change_type:   str,
    old_value:     str,
    new_value:     str,
    document_type: str,
    file_path:     str,
) -> dict:
    """
    Invoke the compiled IASW graph for a new change request.

    Returns:
        {
            "request_id":   str (UUID),
            "final_status": str,
            "score_card":   dict | None,
            "error":        str | None,
        }
    """
    request_id = str(uuid.uuid4())
    db = SessionLocal()

    initial_state: WorkflowState = {
        "request_id":    request_id,
        "customer_id":   customer_id,
        "change_type":   change_type,
        "old_value":     old_value,
        "new_value":     new_value,
        "document_type": document_type,
        "file_path":     file_path,
        # Agent outputs — None until populated by nodes
        "validation_valid":      None,
        "validation_error":      None,
        "rps_current_value":     None,
        "extracted_fields":      None,
        "forgery_check":         None,
        "forgery_signals":       None,
        "forgery_reasoning":     None,
        "filenet_ref_id":        None,
        "processing_mode":       None,
        "score_card":            None,
        "staged":                None,
        "final_status":          None,
        "error_message":         None,
        "_db":                   db,
    }

    try:
        final_state = iasw_graph.invoke(initial_state)
        return {
            "request_id":   request_id,
            "final_status": final_state.get("final_status", "UNKNOWN"),
            "score_card":   final_state.get("score_card"),
            "error":        final_state.get("error_message"),
        }
    except Exception as exc:
        db.rollback()
        raise exc
    finally:
        db.close()
