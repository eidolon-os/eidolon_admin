"""Runtime registry schema exports.

The old tenant/user/agent registry contracts are no longer part of the
runtime resolve surface. Import legacy modules directly only while deleting
them; do not re-export them from this package.
"""

from .resolve import ResolvedContext, ResolveDeviceResponse

__all__ = [
    "ResolvedContext",
    "ResolveDeviceResponse",
]
