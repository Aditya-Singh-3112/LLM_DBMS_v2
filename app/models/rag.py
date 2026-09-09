from typing import Optional

from pydantic import BaseModel


class TextbookChunk(BaseModel):
    source: str
    chapter: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    content: str
    similarity_score: float = 0.0


class RetrievalResult(BaseModel):
    passages: list[TextbookChunk]
    from_cache: bool = False