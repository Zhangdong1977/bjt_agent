from pydantic import BaseModel
from datetime import datetime
from typing import List

class KnowledgeDocumentResponse(BaseModel):
    id: str
    filename: str
    file_path: str
    file_size: int
    created_at: datetime
    file_type: str

    class Config:
        from_attributes = True

class KnowledgeDocumentListResponse(BaseModel):
    documents: List[KnowledgeDocumentResponse]
    total: int