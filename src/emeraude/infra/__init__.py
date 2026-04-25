"""Infrastructure layer — I/O, persistence, network, paths.

This layer isolates side-effecting code from the pure-domain `agent.*` packages.
Modules here own filesystem, database, HTTP, and crypto operations.
"""
