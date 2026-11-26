from typing import Any, Literal

from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=2, description="Câu hỏi cần truy vấn từ kho tài liệu")
    top_k: int = Field(default=5, ge=1, le=20)
    mode: str = Field(default="hybrid", pattern="^(hybrid|dense)$")
    neighbor_window: int = Field(default=1, ge=0, le=3)
    min_score: float = Field(default=0.0, ge=0.0)
    filename: str | None = Field(default=None, description="Lọc theo tên file cụ thể (tùy chọn)")
    heading_contains: str | None = Field(default=None, description="Lọc theo tiêu đề mục trong metadata (tùy chọn)")
    session_id: str | None = Field(default=None, description="Phiên hội thoại cần ghi nhớ. Bỏ trống để chạy stateless.")
    history: list[HistoryMessage] | None = Field(
        default=None,
        description="Lịch sử stateless. Bị bỏ qua khi có session_id.",
    )


class CitationItem(BaseModel):
    source_id: int
    chunk_id: int | None = None
    document_id: int | None = None
    filename: str | None = None
    chunk_index: int | None = None
    heading_path: str | None = None
    content: str | None = None
    is_hit: bool = True
    score: float = 0.0


class QueryResponse(BaseModel):
    question: str
    rewritten_query: str = ""
    mode: str
    answer: str
    grounded: bool
    citations: list[CitationItem]
    raw_hit_count: int
    context_chunk_count: int
    session_id: str | None = None
    latency_ms: dict[str, float]


class SessionCreate(BaseModel):
    title: str | None = None
    namespace: str = "default"


class SessionMessage(BaseModel):
    id: int
    role: str
    content: str
    rewritten_query: str | None = None
    citations: list[CitationItem] = Field(default_factory=list)
    latency_ms: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None


class SessionItem(BaseModel):
    id: str
    title: str
    namespace: str
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SessionDetail(SessionItem):
    messages: list[SessionMessage]


class PageCreate(BaseModel):
    title: str = ""
    parent_id: str | None = None
    icon: str = "📄"


class PagePatch(BaseModel):
    title: str | None = None
    parent_id: str | None = None
    icon: str | None = None
    favorite: bool | None = None
    archived: bool | None = None
    position: float | None = None
    version: int | None = None


class BlockInput(BaseModel):
    id: str
    type: str
    content: dict[str, Any] = Field(default_factory=dict)
    position: float = 0


class SaveBlocksRequest(BaseModel):
    version: int
    blocks: list[BlockInput] = Field(default_factory=list, max_length=400)


class PageAiRequest(BaseModel):
    mode: Literal["ask", "summarize", "continue", "translate"] = "ask"
    prompt: str = ""
