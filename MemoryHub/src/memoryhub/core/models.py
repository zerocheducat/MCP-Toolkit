from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


def timestamp(value: str) -> str:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Use ISO 8601 with a timezone, e.g. 2026-09-30T22:00:00+08:00")
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


class MemoryProposal(BaseModel):
    """The host supplies semantic judgments; the server enforces invariants."""
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str = Field(min_length=1, max_length=4000)
    topic: str = Field(min_length=1, max_length=200, description="Exact semantic key. Reuse the topic returned by recall without changing case or punctuation.")
    source_text: str = Field(min_length=1, max_length=16000, description="Verbatim user message or tool result, never reconstructed by the assistant.")
    evidence: str = Field(min_length=1, max_length=4000, description="Exact contiguous quote from source_text supporting this one memory.")
    reason: str = Field(min_length=1, max_length=1000, description="Why this helps future conversations and why this action is justified.")
    worth_remembering: bool
    namespace: str = Field(default="default", min_length=1, max_length=200)
    kind: Literal["fact", "preference", "constraint", "plan", "event"] = "fact"
    source_role: Literal["user", "tool", "assistant"] = "user"
    basis: Literal["explicit", "inferred", "hypothetical"] = "explicit"
    durability: Literal["durable", "temporary"] = "durable"
    action: Literal["auto", "confirm", "replace", "reject"] = "auto"
    target_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    effective_at: str | None = Field(default=None, description="When it became true; timezone required. Omit when only observation time is known.")
    expires_at: str | None = Field(default=None, description="Required for temporary memories. Timezone required.")
    source_ref: str | None = Field(default=None, max_length=500, description="Real message/result ID if supplied by the host, otherwise omit; never invent.")

    @field_validator("content", "topic", "namespace", "source_text", "evidence", "reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Must not be blank")
        return value

    @field_validator("effective_at", "expires_at")
    @classmethod
    def valid_time(cls, value: str | None) -> str | None:
        return timestamp(value) if value is not None else None
