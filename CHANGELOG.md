# Changelog

All notable changes to StructuralOffice are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.7.0-alpha] - 2026-08-16

### Added

- Database schema version 4 with persistent reminder deliveries and configurable
  non-working dates for recurrence rules.
- Standalone task creation and detailed task retrieval with checklist snapshots.
- Revision-protected updates for task status, priority, due time, completion note, and
  individual checklist items.
- Task start/completion timestamps, completing-user attribution, checklist notes, and
  audit/change events.
- Edit-presence support for tasks, checklist items, and accounting rules.
- Effective reminder catch-up policies: configured window, latest eligible reminder, or
  skip missed reminders.
- Per-routine IANA timezone validation and scheduling.
- Paginated import history, row-level fingerprint details, and administrator-only source
  downloads.
- Automatic safety backup before an existing database schema is migrated.
- OpenAPI 3.1 specification covering the Windows-client REST surface.
- Tests for migration backup, standalone tasks, optimistic task/checklist conflicts,
  non-working dates, import history, and restart-safe reminder delivery.

### Changed

- Manual task state changes are synchronized with their grouped accounting batch.
- Manually progressed or completed accounting tasks are no longer reopened by a routine
  refresh while their membership is unchanged.
- The frontend cache and integration version are now `0.7.0-alpha`.

### Security

- Original invoice-import sources require the StructuralOffice administrator role.
- Task and checklist writes require editor access and an expected revision.

## [0.6.0-alpha] - 2026-08-16

### Added

- Database schema version 3 with normalized workflow topics, ordered topic steps,
  routines, recurrence rules, routine/topic assignments, reminders, task occurrences,
  and task checklist snapshots.
- Persistent materialization of recurring tasks across a rolling history and planning
  window.
- Topic priority, instructions, estimated duration, enabled state, and structured step
  metadata for the future Windows client.
- Routine timezone, end date, catch-up policy, invalid-month-day handling, and previous-
  or next-business-day adjustment.
- Normalized accounting invoices, configurable follow-up rules, grouped accounting task
  batches, and exact task-to-invoice memberships.
- Default server rules for payment-reminder and three dunning task stages.
- Automatic creation of one accounting follow-up task per stage, original due date, and
  currency when matching receivables remain unpaid.
- Automatic completion when all invoices linked to a grouped task are no longer open.
- Stable CSV row fingerprints and import-batch counters for known and new booking rows.
- REST endpoints for materialized tasks, accounting batches, exact invoice membership,
  and revision-protected accounting-rule updates.
- Explicit document generation from an accounting task's exact open invoice membership.
- Migration and behavior tests for schema version 3, recurrence edge cases, grouped
  accounting tasks, automatic completion, and row-level import deduplication.

### Changed

- Removed all invoice statistics from the Home Assistant panel. Invoice information
  remains available only through the authenticated Windows-client API.
- Removed the CSV-import counter from Home Assistant statistics and sensors; the panel
  now displays only database size, backup count, schema version, and integrity.
- Applying an identical CSV source a second time returns a successful no-op result.
- Unchanged invoice records are skipped during imports instead of being rewritten.
- The frontend cache and integration version are now `0.6.0-alpha`.
- The database schema is now version 3 and migrates existing version-1 and version-2
  installations in place.

### Security

- Grouped documents use stored invoice IDs rather than inferring membership from an
  invoice-number range.

### Important behavior

- StructuralOffice creates follow-up tasks automatically, but never creates or sends
  payment-reminder or dunning documents without an explicit authorized request.

## [0.5.0-alpha] - 2026-08-16

### Added

- Revisioned live editing for contacts, topics, routines, generated occurrences, and
  invoices through the authenticated REST API.
- Transactional optimistic concurrency control using an expected record revision.
- Automatic field-level merging when intervening changes do not overlap.
- HTTP 409 conflict responses containing the current server record when fields overlap.
- Expiring edit-presence sessions with multiple visible editors per record.
- Role-protected WebSocket subscription for immediate record and presence events.
- Persistent change-event sequence numbers for reconnect catch-up.
- Audit metadata for live creates, updates, merges, and archives.
- Paginated live-record, event, audit, and REST role-management endpoints.
- Contact records and live task-status editing.
- Concurrent-writer and version-1-to-version-2 database migration tests.

### Changed

- The database schema is now version 2.
- Deletion through the live API archives business records instead of removing them.
- Existing snapshot writes preserve record revisions rather than recreating every row.
- The frontend cache and integration version are now `0.5.0-alpha`.

### Security

- Live WebSocket subscriptions enforce StructuralOffice roles before registration.
- Live events omit business payloads; authorized clients fetch records through the API.
- Audit entries exclude access tokens and complete record payloads.

## [0.4.0-alpha] - 2026-08-16

### Added

- Dedicated, schema-versioned SQLite database outside the Home Assistant recorder.
- One-time migration path from the earlier Home Assistant JSON storage.
- Versioned and Home Assistant-authenticated REST API for the future Windows client.
- Invoice-list CSV parser for semicolon-delimited UTF-8 and Windows-1252 exports.
- Server-side consolidation by invoice number with leading-zero preservation.
- Cancellation detection from negative open amounts in column J.
- Freely configurable default payment term and optional SEPA due-date override.
- Import previews, SHA-256 duplicate detection, and import-batch audit records containing
  the original source bytes.
- Explicit single, multiple, and invoice-number-range document generation.
- Managed SQLite backup creation, listing, download, integrity-checked restoration,
  safety backup creation, and deletion.
- Home Assistant sensors for database size, record count, backup count, and CSV imports.

### Changed

- The Home Assistant panel is now an administrator-only backend dashboard containing
  database statistics, invoice due-date counts, and backup controls.
- Invoice due states are calculated for presentation in the Windows client.
- Existing alpha records are persisted to SQLite after migration.
- The frontend cache version is now `0.4.0-alpha`.

### Removed

- Automatic accounting payment-reminder notifications and automatic dunning escalation.
- Operational topic, routine, accounting, analytics, and role-editing views from the
  Home Assistant panel.
- The Home Assistant calendar platform; operational schedules remain backend data for
  the future Windows client.

### Security

- REST endpoints require Home Assistant authentication and enforce StructuralOffice
  roles server-side.
- Backup management and the Home Assistant panel require administrator access.
- Backup filenames are strictly validated before file access.

### Documentation

- Project documentation, release notes, security guidance, and development guidance are
  now maintained in English.
- Backend validation messages, notifications, entity names, and release-tool output now
  use English.
- Generated PDF, Excel, and CSV documents now use English labels and filenames.
- The default Home Assistant translation source is English; German remains available as
  a localized translation.

### Fixed

- Sorted integration manifest keys in the order required by Hassfest.
- Declared the integration as config-entry-only to satisfy Home Assistant schema
  validation and explicitly reject YAML configuration.

## [0.3.0-alpha] - 2026-08-16

### Added

- Rolling twelve-month accounting analytics
- Analytics for completed, open, and skipped tasks
- Aging buckets for open receivables
- PDF payment reminders and first, second, and third dunning letters
- Company details for generated documents
- Administrator, editor, and viewer role management
- Neutral, Excel-compatible CSV export
- Contact addresses for accounting records and dunning documents

### Changed

- Authorized non-administrator users can access the StructuralOffice panel.
- Server-side role checks protect every write operation.
- The panel includes dedicated analytics and role-management sections.
- The frontend cache version is now `0.3.0-alpha`.

### Security

- Users without an assigned role cannot access StructuralOffice data.
- Viewers can read and export data but cannot modify it.
- Only administrators can manage roles or send test notifications.

## [0.2.0] - 2026-08-16

### Added

- Direct management of payable and receivable invoices
- Net, tax, and gross amounts, payment states, and dunning levels
- Due-payment reminders and multi-stage dunning schedules
- Validated Excel template with an import preview
- Excel import and export with stable record IDs
- Accounting dashboard metrics and additional Home Assistant sensors

## [0.1.0] - 2026-08-16

### Added

- Initial HACS-compatible StructuralOffice integration
- Dedicated Home Assistant panel
- Reusable topics with descriptions, categories, and checklists
- One-time, daily, weekly, monthly, and yearly routines
- Multiple due dates and reminder offsets per routine
- Push notifications to selected `notify` entities
- Task states, sensors, calendar support, and local storage

[Unreleased]: https://github.com/jl0906/StructuralOffice/compare/v0.7.0-alpha...HEAD
[0.7.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.6.0-alpha...v0.7.0-alpha
[0.6.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.5.0-alpha...v0.6.0-alpha
[0.5.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.4.0-alpha...v0.5.0-alpha
[0.4.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/jl0906/StructuralOffice/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jl0906/StructuralOffice/releases/tag/v0.1.0
