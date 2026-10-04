"""Ticket state machine. A ticket stays live until the customer confirms the fix (or an admin resolves
with a written note); any "it didn't work" moves the *same* ticket back to a human with its history."""

from __future__ import annotations

from datetime import timedelta

STATUSES = {
    "analyzing": "Analyzing your request",
    "self_service": "Solution ready - try these steps",
    "escalated": "With our support team",
    "in_progress": "Admin working on it",
    "awaiting_customer": "Waiting for your reply",
    "solution_proposed": "Solution proposed - please confirm",
    "resolved": "Resolved",
    "closed": "Closed",
}

TRANSITIONS: dict[str, set[str]] = {
    "analyzing": {"self_service", "escalated"},
    "self_service": {"resolved", "escalated", "in_progress"},
    "escalated": {"in_progress", "awaiting_customer", "solution_proposed", "resolved"},
    "in_progress": {"awaiting_customer", "solution_proposed", "resolved", "escalated"},
    "awaiting_customer": {"in_progress", "solution_proposed", "resolved", "escalated"},
    "solution_proposed": {"resolved", "in_progress", "awaiting_customer"},
    "resolved": {"in_progress", "closed"},
    "closed": set(),
}

OPEN = {"analyzing", "self_service", "escalated", "in_progress", "awaiting_customer", "solution_proposed"}
HUMAN_QUEUE = {"escalated", "in_progress", "awaiting_customer", "solution_proposed"}

SLA = {"P1": timedelta(hours=4), "P2": timedelta(hours=24), "P3": timedelta(hours=48), "P4": timedelta(hours=72)}


class InvalidTransition(ValueError):
    pass


def check_transition(current: str, target: str) -> None:
    if target == current:
        return
    if target not in TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"Cannot move a ticket from '{current}' to '{target}'")
