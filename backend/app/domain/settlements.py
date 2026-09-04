"""Controlled lifecycle states for merchant settlements."""

from enum import StrEnum


class SettlementStatus(StrEnum):
    PENDING = "pending"
    BROADCASTING = "broadcasting"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REVIEW_REQUIRED = "review_required"
    CANCELLED = "cancelled"
