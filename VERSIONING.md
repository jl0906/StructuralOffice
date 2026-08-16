# Versioning and release status

StructuralOffice uses semantic versioning. All published builds remain explicit
prereleases while the integration and Windows application are under development.

## Current status

`0.4.0-alpha`

This version is intended for development and testing. It does not guarantee a stable
database schema or a permanently compatible API.

## Release stages

- **Alpha:** The data model, API, Home Assistant backend, or Windows application is
  incomplete. Breaking changes and database migrations remain possible.
- **Beta:** Every feature planned for `1.0.0` is implemented. Work focuses on
  stabilization, migration testing, and real-world validation.
- **Release candidate:** Features and APIs are frozen. Only release-blocking defects
  are addressed.
- **Stable:** Approved for production use.

## Requirements for 1.0.0

Version `1.0.0` will only be published when all of the following are complete:

- Home Assistant stores all StructuralOffice data persistently in the dedicated database.
- Topics, routines, tasks, contacts, invoices, reminders, and dunning workflows are fully
  supported.
- The database schema and versioned API are stable and documented.
- Backup creation, integrity checks, and restoration are tested.
- Migration of existing alpha data is verified.
- The Home Assistant panel provides system status, database statistics, and backups.
- The Windows application supports every planned operational workflow.
- Multi-user access, roles, and conflict handling are tested.
- Home Assistant restarts and connection interruptions cannot cause data loss.
- Installation, upgrade, and end-to-end tests pass.

Versions below `1.0.0` communicate development progress but do not promise a stable API.
