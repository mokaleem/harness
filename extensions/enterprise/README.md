# DeerFlow enterprise extension

An independently packaged capability registry, reviewed skill publication workflow,
dependency recovery middleware, and approved remote sandbox adapter.

See the [setup and contract guide](../../docs/enterprise-services.md) for native
Windows/macOS installation, registry projection, Python APIs, validation hooks,
Typesense exports, and the remote broker protocol. The host requires extension API
0.2.5. SQLite is for local development; PostgreSQL shares enterprise state.

The package uses DeerFlow's engine and native package reviewer. It does not
implement a second agent runtime or provision enterprise infrastructure.
