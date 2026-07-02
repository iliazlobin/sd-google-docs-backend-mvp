"""Pure OT transform functions — the 4-way matrix for Jupiter protocol.

All functions are pure: no I/O, no DB access. Take two operations and return
transformed versions suitable for sequential application.

Each function takes two operations (a=concurrent, b=client_op) and transforms b
against a. The caller is responsible for iterating over all concurrent ops.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OpType = Literal["insert", "delete"]


@dataclass
class Op:
    type: OpType
    position: int
    text: str | None = None  # for insert
    length: int | None = None  # for delete


def _shift(op: Op, delta: int) -> Op:
    """Return a copy of op with position shifted by delta."""
    return Op(type=op.type, position=op.position + delta, text=op.text, length=op.length)


def insert_insert(concurrent: Op, client: Op) -> Op | None:
    """Transform client insert against a concurrent insert.

    Rule: if concurrent is before client, client shifts right.
    If concurrent is at same position or after, client shifts right too (tiebreak:
    server-arrival order — concurrent arrived first, client shifts).
    """
    if concurrent.position <= client.position:
        return _shift(client, len(concurrent.text or ""))
    return client


def insert_delete(concurrent: Op, client: Op) -> Op | None:
    """Transform client delete against a concurrent insert.

    If concurrent insert is before or at client's delete position,
    shift the delete position right. If insert is inside the delete range,
    it doesn't affect the delete (the delete already covers those chars).
    """
    if concurrent.position <= client.position:
        return _shift(client, len(concurrent.text or ""))
    # If insert is inside the delete range, the delete still deletes the
    # original chars — but the insert chars weren't there when the delete
    # was issued, so they survive. We need to shrink the delete's reach.
    if concurrent.position < client.position + (client.length or 0):
        # Insert inside delete range — the insert's chars survive,
        # delete covers original chars only (no position shift needed).
        # The delete still operates on the original text region.
        return client
    return client


def delete_insert(concurrent: Op, client: Op) -> Op | None:
    """Transform client insert against a concurrent delete.

    If concurrent delete is entirely before client insert,
    shift client insert left by delete length.
    If concurrent delete is at or after client insert position and overlaps,
    the insert position shifts into or past the deleted region.
    """
    concurrent_end: int = concurrent.position + (concurrent.length or 0)

    if concurrent_end <= client.position:
        # Delete is entirely before insert → shift insert left
        return _shift(client, -(concurrent.length or 0))
    if concurrent.position >= client.position + len(client.text or ""):
        # Delete is entirely after insert → no change
        return client
    # Delete overlaps insert position → shift insert to start of delete
    # (the chars before the delete survive, insert happens at delete position)
    delta = concurrent.position - client.position
    return _shift(client, delta)


def delete_delete(concurrent: Op, client: Op) -> Op | None:
    """Transform client delete against a concurrent delete.

    Two overlapping deletes: the concurrent delete removes chars first,
    so the client's delete range shrinks.
    """
    concurrent_end: int = concurrent.position + (concurrent.length or 0)
    client_end: int = client.position + (client.length or 0)

    if concurrent_end <= client.position:
        # Concurrent delete is entirely before client → shift client left
        return _shift(client, -(concurrent.length or 0))
    if concurrent.position >= client_end:
        # Concurrent delete is entirely after client → no change
        return client
    # Overlapping deletes
    # The concurrent range was deleted first, so the client delete range shrinks
    # by the overlapping portion
    new_position = min(client.position, concurrent.position)
    # New end is the union of both minus the overlap already removed
    overlap_start = max(client.position, concurrent.position)
    overlap_end = min(client_end, concurrent_end)
    overlap_len = max(0, overlap_end - overlap_start)
    new_length = (client.length or 0) - overlap_len
    if new_length <= 0:
        return None  # Client's delete was completely covered
    return Op(type="delete", position=new_position, length=new_length)
