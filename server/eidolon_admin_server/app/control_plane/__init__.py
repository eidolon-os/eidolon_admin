"""Admin-owned adapters and orchestration for Eidolon control authorities.

The package deliberately has no eager exports.  The independent lifecycle
process imports the protocol and shared contracts without constructing the
Admin router/service graph; applications import those concrete modules
explicitly at their composition root.
"""
