"""Conservative admission policy after semantic assessment by the Jan model."""
from datetime import datetime, timedelta
from memoryhub.core.models import MemoryProposal


def assess(p: MemoryProposal, now: str) -> tuple[str, str, str | None]:
    if p.evidence not in p.source_text:
        raise ValueError("evidence must be an exact contiguous substring of source_text")
    if p.action == "reject" or not p.worth_remembering:
        return "rejected", "Not useful for future conversations", None
    if p.basis == "hypothetical":
        return "rejected", "A hypothetical statement is not an established personal fact", None
    effective = p.effective_at or now
    if effective > now:
        raise ValueError("effective_at cannot be in the future; record a future event as a present plan")
    if p.durability == "temporary" and p.expires_at is None:
        raise ValueError("Temporary memory requires an explicit expires_at")
    if p.expires_at and p.expires_at <= effective:
        raise ValueError("expires_at must be after effective_at")
    if p.source_role == "assistant" or p.basis == "inferred":
        expiry = p.expires_at or (datetime.fromisoformat(now) + timedelta(days=7)).isoformat(timespec="microseconds")
        return "pending", "Inference or assistant text requires independent confirmation", expiry
    if p.expires_at and p.expires_at <= now:
        return "expired", "Already outside its validity period", p.expires_at
    return "active", "Explicit evidence admitted", p.expires_at
