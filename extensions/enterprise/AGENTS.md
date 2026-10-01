# Enterprise extension

This separately packaged extension owns capability revisions, activation pointers,
evidence drafts, release review, and dependency circuit state. Its only DeerFlow
integration points are the public extension API, native package review, and the
config, middleware, skill-storage and sandbox adapters. These `deerflow.*`
imports are version-specific host coupling covered by the enterprise tests;
they are not all guaranteed by the public extension contract. Do not import
`app.*` or duplicate the agent loop.

Registry revisions and released skill packages are immutable. Activation is an
explicit administrator operation. Team visibility uses authenticated `team:<id>`
roles; request bodies never supply identity. Reviewers cannot approve their own
drafts. Every review binds the exact validated content digest. Filesystem output
and Typesense are replaceable projections, never competing writers.

Use SQLite for native single-host development and PostgreSQL for shared deployments.
Tests live in `backend/tests/test_enterprise_*.py` and use synthetic evidence and
mock remote transports. Never replay writes after an ambiguous dependency timeout.
Remote resource and network limits must be enforced by the approved broker, not
by agent instructions. Do not claim a broker is approved merely because it answers.
