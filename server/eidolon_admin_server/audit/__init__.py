"""Admin's rebuildable projection of the global audit stream.

Not an authority: the facts are owned by each producer's outbox, and this is a
query index over what they published. It is filled by :func:`run_audit_indexer`
running inside the Admin process that reads it — there is no second entry point
and no separate unit, because a second writer to this SQLite file is the one
thing the design does not tolerate.
"""

from .index import (
    AuditIndexSettings,
    AuditIndexStore,
    IndexedAuditEvent,
    default_audit_index_path,
)
from .runner import run_audit_indexer

__all__ = [
    "AuditIndexSettings",
    "AuditIndexStore",
    "IndexedAuditEvent",
    "default_audit_index_path",
    "run_audit_indexer",
]
