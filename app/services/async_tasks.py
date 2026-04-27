"""
async_tasks.py — Background task management for long-running operations.

Manages the execution of the IASW pipeline asynchronously in the background,
allowing the HTTP request to return immediately (202 Accepted).

Tasks:
  - run_pipeline_task: Execute full IASW pipeline in background
  - get_task_status: Retrieve task status and result

Task storage: In-memory dict (for MVP). For production, use Celery + Redis.
"""

import uuid
import logging
from datetime import datetime
from typing import Any, Dict, Optional
from enum import Enum
import asyncio

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task execution states."""
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Task:
    """Represents a background task."""
    
    def __init__(self, task_id: str, name: str, payload: dict):
        self.id = task_id
        self.name = name
        self.payload = payload
        self.status = TaskStatus.QUEUED
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        """Serialize task to dict."""
        return {
            "task_id": self.id,
            "name": self.name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class TaskManager:
    """Manages background task execution and storage."""
    
    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.lock = asyncio.Lock()
    
    async def create_task(self, name: str, payload: dict) -> str:
        """Create and enqueue a new task. Returns task_id."""
        task_id = str(uuid.uuid4())
        task = Task(task_id, name, payload)
        
        async with self.lock:
            self.tasks[task_id] = task
        
        logger.info(f"Task created: {task_id} ({name})")
        return task_id
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieve task by ID."""
        async with self.lock:
            return self.tasks.get(task_id)
    
    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> bool:
        """Update task status and result."""
        async with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                return False
            
            task.status = status
            if result:
                task.result = result
            if error:
                task.error = error
            
            if status == TaskStatus.RUNNING:
                task.started_at = datetime.utcnow()
            elif status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
                task.completed_at = datetime.utcnow()
            
            logger.debug(f"Task {task_id} status → {status.value}")
            return True
    
    async def delete_task(self, task_id: str) -> bool:
        """Delete task from storage."""
        async with self.lock:
            if task_id in self.tasks:
                del self.tasks[task_id]
                logger.debug(f"Task {task_id} deleted")
                return True
            return False
    
    def get_all_tasks(self) -> list:
        """Get all tasks (for monitoring)."""
        return [task.to_dict() for task in self.tasks.values()]


# Global task manager instance
task_manager = TaskManager()


# ── Background task executors ──────────────────────────────────────────────────

async def execute_pipeline_task(task_id: str, payload: dict):
    """
    Execute IASW pipeline in background.
    
    Args:
        task_id: Task identifier
        payload: Dict with customer_id, change_type, etc.
    """
    try:
        await task_manager.update_task_status(task_id, TaskStatus.RUNNING)
        
        # Import here to avoid circular imports
        from app.agents.graph import run_iasw_pipeline
        
        # ── Critical: run synchronous pipeline in a thread pool ───────────────
        # run_iasw_pipeline is a synchronous blocking function (LangGraph +
        # Gemini API calls). Calling it directly in an async context blocks the
        # entire event loop — meaning polling requests (GET /api/tasks/{id})
        # cannot be served while Gemini is processing.
        #
        # run_in_executor(None, ...) uses the default ThreadPoolExecutor,
        # offloading the blocking work to a worker thread while the event loop
        # remains free to handle incoming poll requests.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,                          # use default thread pool
            lambda: run_iasw_pipeline(
                customer_id=payload["customer_id"],
                change_type=payload["change_type"],
                old_value=payload["old_value"],
                new_value=payload["new_value"],
                document_type=payload["document_type"],
                file_path=payload["file_path"],
            )
        )
        
        # Update task with result
        await task_manager.update_task_status(
            task_id,
            TaskStatus.COMPLETED,
            result=result,
        )
        
        logger.info(f"Pipeline task {task_id} completed")
    
    except Exception as e:
        error_msg = f"Pipeline execution failed: {str(e)}"
        logger.error(f"Task {task_id} failed: {error_msg}")
        
        await task_manager.update_task_status(
            task_id,
            TaskStatus.FAILED,
            error=error_msg,
        )


async def enqueue_pipeline(payload: dict) -> str:
    """
    Enqueue a new pipeline execution task.
    
    Args:
        payload: Dict with all required fields
    
    Returns:
        task_id for polling status
    """
    task_id = await task_manager.create_task("run_pipeline", payload)
    
    # Fire-and-forget background execution
    # In production, use Celery to queue to Redis
    asyncio.create_task(execute_pipeline_task(task_id, payload))
    
    return task_id
