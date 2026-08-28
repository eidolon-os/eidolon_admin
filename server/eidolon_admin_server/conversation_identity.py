"""The Agent-owned opaque conversation identity, as Admin carries it.

Admin neither creates nor interprets this value. The single bound here keeps
the upstream consumer, internal ABI and public Owner projection from silently
narrowing one another while still bounding every transport surface.
"""

CONVERSATION_ID_MAX_LENGTH = 512
