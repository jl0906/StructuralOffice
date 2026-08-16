# Changelog

All notable changes to StructuralOffice are documented in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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

[Unreleased]: https://github.com/jl0906/StructuralOffice/compare/v0.4.0-alpha...HEAD
[0.4.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/jl0906/StructuralOffice/compare/v0.2.0...v0.3.0-alpha
[0.2.0]: https://github.com/jl0906/StructuralOffice/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jl0906/StructuralOffice/releases/tag/v0.1.0
