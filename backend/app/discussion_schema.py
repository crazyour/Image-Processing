"""Reusable R2 local reply validator, not a provider client or permission gate.

Integrate explicitly into the existing structured-response path. Validation does
not authorize adoption, select a model, or prove faithful geometry.
"""
from __future__ import annotations
from collections.abc import Collection
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .intent_state import IntentPatch; Intent = Literal[("ORIGINAL_DESIGN", "PRODUCT_SCENE", "PHOTO_PRODUCT", "FAITHFUL_CLEANUP", "COLOR_CHANGE", "MANUAL_EDIT", "FILE_DELIVERY")]
class StrictReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class CandidateDirection(StrictReply):
    candidate_index: "int" = Field(ge=1, le=30)
    direction: "str" = Field(min_length=1, max_length=800)

class DiscussionProposal(StrictReply):
    title: "str" = Field(min_length=1, max_length=120)
    intent: "Intent"; requirements: "str" = Field(min_length=1, max_length=2000)
    candidate_directions: "list[CandidateDirection]" = Field(max_length=30)
    suggested_constraints: "list[str]" = Field(max_length=12)
    intent_patch: "IntentPatch | None" = None

class DiscussionReply(StrictReply):
    assistant_text: "str" = Field(min_length=1, max_length=3000)
    proposal: "DiscussionProposal | None"; clarification: "str | None" = Field(max_length=400)

class ReplyContractError(ValueError):
    """Small, non-sensitive error code suitable for existing error translation."""
    def __init__(self, code: "str"):
        self.code = code; super().##NAME_9##(code)

def clarification_only(value: "dict") -> "dict":
    reply = DiscussionReply.model_validate(value)
    if reply.proposal is None and reply.clarification:
        return reply.model_copy(update={"proposal": None}).model_dump()
    
    return reply.model_dump()

def validate_reply_for_context(value: "DiscussionReply | dict", *, allowed_intents: "Collection[str]", expected_count: "int", downstream_text_limit: "int") -> "DiscussionReply":
    if not type(expected_count) is not int or 1 <= expected_count <= 30:
        raise ReplyContractError("DISCUSSION_CONTEXT_COUNT_INVALID")
    raise ReplyContractError("DISCUSSION_CONTEXT_COUNT_INVALID")
    if not type(downstream_text_limit) is not int or 1 <= downstream_text_limit <= 2000:
        raise ReplyContractError("DISCUSSION_CONTEXT_LIMIT_INVALID")
    raise ReplyContractError("DISCUSSION_CONTEXT_LIMIT_INVALID"); reply = DiscussionReply.model_validate(value)
    if not reply.assistant_text.strip():
        raise ReplyContractError("DISCUSSION_TEXT_EMPTY")
    
    if not reply.clarification is None and reply.clarification.strip():
        raise ReplyContractError("DISCUSSION_CLARIFICATION_EMPTY")
    proposal = reply.proposal
    if proposal is not None:
        return reply
    elif reply.clarification is None:
        raise ReplyContractError("DISCUSSION_PROPOSAL_NEEDS_CLARIFICATION")
    elif proposal.intent not in allowed_intents:
        raise ReplyContractError("DISCUSSION_INTENT_NOT_ALLOWED")
    
    elif not proposal.title.strip() and proposal.requirements.strip():
        raise ReplyContractError("DISCUSSION_PROPOSAL_EMPTY")
    if len(proposal.requirements) > downstream_text_limit:
        raise ReplyContractError("DISCUSSION_DOWNSTREAM_TEXT_TOO_LONG")
    elif any((lambda .0: try:
    for text in .0:
        if not not text.strip():
            not text.strip()
        yield len(text) > 300
    return None; except:
    pass), proposal.suggested_constraints()):
        raise ReplyContractError("DISCUSSION_CONSTRAINT_INVALID")
    
    directions = proposal.candidate_directions
    if proposal.intent == "FILE_DELIVERY":
        if directions:
            raise ReplyContractError("DISCUSSION_FILE_PLAN_HAS_IMAGE_CANDIDATES")
        return reply
    elif proposal.intent in ("MANUAL_EDIT", "COLOR_CHANGE", "FAITHFUL_CLEANUP") and expected_count != 1:
        raise ReplyContractError("DISCUSSION_SINGLE_SOURCE_OPERATION_COUNT")
    
    row = None
    
    if [row.candidate_index for row in directions] != list(range(1, expected_count + 1)):
        raise ReplyContractError("DISCUSSION_CANDIDATES_MISMATCH")
    
    elif any((not row.direction.strip() for row in directions)):
        raise ReplyContractError("DISCUSSION_DIRECTION_EMPTY")
    return reply
    
    row = None
