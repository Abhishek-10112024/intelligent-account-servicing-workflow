"""
filenet_mock.py — Mock FileNet document archival service.

In production, this would call IBM FileNet APIs to:
  - Upload the document blob
  - Attach structured metadata
  - Return a Content Engine object ID

For this prototype, we save the file to the local 'uploads/' directory
and generate a deterministic reference ID.  The schema of the metadata
dict mirrors what we would send to a real FileNet Content Engine.
"""

import shutil
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.observability import get_logger

logger = get_logger("filenet_mock")


def archive_document(
    source_path: Path,
    customer_id: str,
    change_type: str,
    document_type: str,
    request_id: str,
) -> dict:
    """
    Archive a document to the mock FileNet store.

    Args:
        source_path:   Path to the temp-uploaded file
        customer_id:   Bank customer ID
        change_type:   e.g. LEGAL_NAME_CHANGE
        document_type: e.g. MARRIAGE_CERTIFICATE
        request_id:    UUID of the PendingRequest

    Returns:
        dict with 'ref_id' (string) and 'metadata' (dict)
    """
    # Build the destination path: uploads/<customer_id>/<request_id>/<filename>
    dest_dir = settings.FILENET_UPLOAD_DIR / customer_id / request_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source_path.name

    shutil.copy2(source_path, dest_path)

    # FileNet reference ID — in production this is the CE object ID
    ref_id = f"FN-{str(uuid.uuid4()).upper()[:12]}"

    metadata = {
        "ref_id":        ref_id,
        "customer_id":   customer_id,
        "change_type":   change_type,
        "document_type": document_type,
        "request_id":    request_id,
        "file_path":     str(dest_path),
        "file_name":     source_path.name,
        "archived_at":   datetime.utcnow().isoformat(),
        "archived_by":   "iasw_document_processor",
        # In production: document_class, folder_path, security_group, retention_policy
    }

    logger.info(
        "FILENET_ARCHIVED",
        ref_id=ref_id,
        file=source_path.name,
        customer_id=customer_id,
    )

    return {"ref_id": ref_id, "metadata": metadata}
