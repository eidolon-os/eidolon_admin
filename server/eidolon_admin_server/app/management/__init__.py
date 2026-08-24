"""Owner management use cases, hosted in the process that holds the credentials.

The public surface is in Local API — that is the boundary a Controller session
authenticates against. The use cases are here because this is the process that
holds the authority service credentials, and the LAN-facing one must not (see
the plan's §3.4.1). What crosses between them is a narrow internal ABI, not a
database handle.
"""
