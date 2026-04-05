"""Events API routes for SSE streams."""

import json
import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from backend.services.sse_service import sse_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Events"])


@router.get("/api/events/documents/{document_id}/stream")
async def stream_document_parse_events(document_id: str):
    """Stream SSE events for document parsing progress.

    Events are published by the Celery parse_document task to Redis Stream
    sse:stream:doc_parse:{document_id}.
    """
    logger.info(f"[events] SSE connection requested for document parse: {document_id}")

    async def event_generator():
        stream_key = f"sse:stream:doc_parse:{document_id}"
        try:
            async for raw_event in sse_manager.connect(stream_key):
                yield raw_event
        except Exception as e:
            logger.error(f"[events] SSE stream error for {document_id}: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )