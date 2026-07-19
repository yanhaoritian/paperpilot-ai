from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class SendCodeRequest(BaseModel):
    channel: Literal["email", "phone"]
    target: str = Field(min_length=5, max_length=255)


class SendCodeResponse(BaseModel):
    ok: bool
    channel: str
    target: str
    delivery: str
    expires_in: int
    message: str
    dev_code: str | None = None


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    code: str = Field(min_length=4, max_length=8)
    channel: Literal["email", "phone"]
    email: str | None = None
    phone: str | None = None

    @field_validator("username")
    @classmethod
    def normalize_username(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def require_contact(self) -> "RegisterRequest":
        if self.channel == "email":
            if not self.email or "@" not in self.email:
                raise ValueError("请填写邮箱")
        else:
            if not self.phone:
                raise ValueError("请填写手机号")
        return self


class LoginRequest(BaseModel):
    """Login with email or phone + password."""

    account: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class QuotaOut(BaseModel):
    day: str
    queries_used: int
    queries_limit: int
    uploads_used: int
    uploads_limit: int


class UserOut(BaseModel):
    id: str
    username: str
    email: str | None = None
    phone: str | None = None
    created_at: datetime
    quota: QuotaOut | None = None

    model_config = {"from_attributes": True}


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class LibraryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class LibraryOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime
    document_count: int = 0

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: str
    library_id: str
    file_name: str
    file_hash: str
    status: str
    status_detail: str | None
    page_count: int
    embedding_model: str | None = None
    embedding_version: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class CitationOut(BaseModel):
    chunk_id: str
    document_id: str | None = None
    file_name: str | None = None
    library_id: str | None = None
    excerpt: str
    page_start: int | None = None
    page_end: int | None = None
    score: float | None = None
    section_path: str | None = None


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    library_ids: list[str] = Field(min_length=1)
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=1)


class QueryResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    confidence: str | None = None
    retrieval_hit: int = 0
    degraded: bool = False
    out_of_scope: bool = False


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    library_ids: list[str] = Field(default_factory=list)


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    library_ids: list[str] | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CitationOut] | None = None
    meta: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_msg(cls, msg) -> "MessageOut":  # noqa: ANN001
        return cls(
            id=msg.id,
            role=msg.role,
            content=msg.content or "",
            citations=msg.citations,
            meta=msg.extra,
            created_at=msg.created_at,
        )


class ConversationOut(BaseModel):
    id: str
    title: str
    library_ids: list[str]
    created_at: datetime
    updated_at: datetime | None = None
    message_count: int = 0

    model_config = {"from_attributes": True}


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = Field(default_factory=list)


class ConversationMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    library_ids: list[str] | None = None
    model: str | None = None
    temperature: float = Field(default=0.2, ge=0, le=1)


class HealthResponse(BaseModel):
    status: str
    database: bool
    has_api_key: bool
    detail: dict | None = None
