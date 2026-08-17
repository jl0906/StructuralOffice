# StructuralOffice

StructuralOffice is a local Home Assistant backend for recurring business processes and
invoice due-date monitoring. The operational desktop interface is being moved to a
separate Windows application. Home Assistant now provides database health statistics
and managed backup controls only.

## Version 0.9.0-beta

This release completes the main backend task lifecycle and stabilizes the contract needed
to start the Windows-client beta work:

- Dedicated, versioned SQLite database at
  `/config/structuraloffice/structuraloffice.db`
- One-time migration of existing alpha data from Home Assistant JSON storage
- Authenticated REST API under `/api/structuraloffice/v1`
- Semicolon-separated invoice-list CSV import with UTF-8 and Windows-1252 support
- Invoice-number consolidation while preserving leading zeros
- Cancellation detection when column J (`Offener Rechnungsbetrag`) is negative
- Open, paid, cancelled, due-today, overdue, and upcoming invoice states
- Freely configurable default payment term from 0 to 365 days
- Optional use of the SEPA debit date as the invoice due date
- Duplicate-source protection using a SHA-256 checksum
- Original CSV retention in the import audit record
- Manual payment-reminder and dunning-document generation for individual invoice
  numbers, explicit selections, or an inclusive invoice-number range
- Consistent SQLite backup creation, download, integrity validation, restoration, and
  deletion from the Home Assistant panel
- Database-size, record-count, backup-count, and schema-version sensors
- Revisioned live records for contacts, topics, routines, occurrences, and invoices
- Field-level updates with optimistic concurrency control
- Automatic merging when concurrent clients changed different fields
- `409 Conflict` responses when clients changed the same field
- Expiring edit-presence sessions showing who currently has a record open
- Authorized WebSocket subscriptions for immediate record and presence events
- Persistent event cursors for catching up after a lost connection
- Audit metadata for every live create, update, merge, and archive operation
- REST role administration for the future Windows client
- Normalized workflow topics, ordered topic steps, recurrence rules, routine/topic
  assignments, reminders, materialized tasks, and checklist snapshots
- Monthly and yearly invalid-date handling plus optional previous- or next-business-day
  adjustment, including routines such as paying VAT on the eighth of every month
- A rolling task-materialization window that preserves task history independently of
  later topic changes
- Configurable grouped payment-reminder task generation for overdue invoices
- Exactly one automatic follow-up task per rule, invoice due date, and currency, linked
  to the precise set of unpaid invoices
- Automatic completion of grouped accounting tasks after all linked invoices are paid,
  cancelled, archived, or otherwise no longer open
- Stable row fingerprints and import counters for detecting already imported CSV rows
- No-op handling when the exact same CSV source is applied again
- Document generation from the exact invoice membership of a grouped accounting task,
  only after an explicit request
- Standalone task creation plus revision-protected task and checklist updates
- Completion timestamps, completing-user attribution, and task/checklist notes
- Advisory edit presence for tasks, checklist items, and accounting rules
- Configurable non-working dates and per-routine IANA timezone validation
- Effective `configured_window`, `latest_only`, and `skip_missed` reminder catch-up
  strategies
- Persistent reminder-delivery records that prevent duplicates after a restart
- Paginated import history, row details, and administrator-only source downloads
- Automatic safety backup before every database schema migration
- Machine-readable OpenAPI 3.1 contract for the future Windows client
- Database schema migration from versions 1 through 4 to the release-model schema 5
- Direct routines that create tasks without requiring a separate topic
- Required estimated durations for routines and standalone tasks
- A configurable 10-minute default for each grouped payment-reminder task
- A today-dashboard API returning total estimated office time and the longest due task

StructuralOffice automatically creates one grouped payment-reminder writing task when
receivables remain unpaid after their configured due dates. It never creates or sends
payment-reminder or dunning documents automatically.

## Installation with HACS

Until the repository is included in the default HACS repository list:

1. Add this repository to HACS as a custom repository of type **Integration**.
2. Install **StructuralOffice**.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and select StructuralOffice.
5. Configure the default payment term and whether a SEPA debit date overrides it.

The StructuralOffice sidebar panel is restricted to Home Assistant administrators. It
contains database statistics and backup management only. Invoice statistics, operational
topics, routines, invoice imports, invoice lists, and document workflows are intended
for the Windows application.

## Invoice-list CSV rules

The importer expects the invoice export discussed for this project, including these
columns:

- `Rechnungsnummer`
- `Kundennummer`
- `Rechnungsempfänger`
- `Rechnungsdatum`
- `Netto gesamt`
- `Mehrwertsteuer gesamt`
- `Brutto gesamt`
- `Eingangsdatum`
- `Eingangsbetrag gesamt`
- `Offener Rechnungsbetrag`

Rows are grouped by invoice number. A negative open amount in any row of a group marks
the invoice as cancelled. Otherwise, a positive open amount means open and zero means
paid. When enabled and populated, `Abbuchungstag SEPA` is the due date; otherwise the
due date is the invoice date plus the configured payment term. Changing the default
does not rewrite invoices already imported.

Every source row receives a stable content fingerprint. Preview responses report known
and new rows, unchanged normalized invoices are not rewritten, and applying the exact
same source file again returns a successful no-op result. Import batches retain the
fingerprints needed to detect overlap with later exports.

## REST API for the Windows client

The API uses Home Assistant authentication. Clients send a valid Home Assistant bearer
token in the `Authorization` header. Authorization is additionally limited by the
StructuralOffice administrator, editor, and viewer roles.

The complete request, revision, presence, conflict, and reconnect contract is documented
in [API.md](API.md) and [OPENAPI.yaml](OPENAPI.yaml).

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/structuraloffice/v1/status` | Backend and database status |
| `GET` | `/api/structuraloffice/v1/dashboard/today` | Today's estimated office workload and longest due task |
| `GET` | `/api/structuraloffice/v1/invoices` | Normalized invoices; supports `status` and `due_state` filters |
| `GET` | `/api/structuraloffice/v1/tasks` | Materialized recurring and accounting tasks |
| `POST` | `/api/structuraloffice/v1/tasks` | Create a standalone task |
| `GET/PATCH` | `/api/structuraloffice/v1/tasks/{id}` | Read or revision-protected update of a task |
| `PATCH` | `/api/structuraloffice/v1/tasks/{id}/checklist/{item_id}` | Revision-protected checklist update |
| `GET` | `/api/structuraloffice/v1/accounting/tasks` | Grouped accounting follow-up tasks |
| `GET` | `/api/structuraloffice/v1/accounting/tasks/{id}/invoices` | Exact invoice membership of one grouped task |
| `GET` | `/api/structuraloffice/v1/accounting/rules` | Configurable follow-up task rules |
| `PATCH` | `/api/structuraloffice/v1/accounting/rules/{id}` | Revision-protected rule update |
| `GET/POST` | `/api/structuraloffice/v1/live/{collection}` | Page or create live records |
| `GET/PATCH/DELETE` | `/api/structuraloffice/v1/live/{collection}/{id}` | Read, update, or archive a revisioned record |
| `GET/POST/DELETE` | `/api/structuraloffice/v1/editing/{collection}/{id}` | Read, start, refresh, or end edit presence |
| `GET` | `/api/structuraloffice/v1/events` | Retrieve missed events after a sequence cursor |
| `GET` | `/api/structuraloffice/v1/audit` | Administrator-only audit metadata |
| `GET/PUT` | `/api/structuraloffice/v1/roles` | Administrator-only role management |
| `POST` | `/api/structuraloffice/v1/imports/invoice-list` | Preview or apply a base64-encoded CSV import |
| `GET` | `/api/structuraloffice/v1/imports` | Paginated import history |
| `GET` | `/api/structuraloffice/v1/imports/{id}` | Import metadata and retained row fingerprints |
| `GET` | `/api/structuraloffice/v1/imports/{id}/source` | Administrator-only original source download |
| `POST` | `/api/structuraloffice/v1/documents` | Explicitly generate one PDF or a ZIP batch |
| `GET/POST` | `/api/structuraloffice/v1/backups` | List or create backups |
| `GET/POST/DELETE` | `/api/structuraloffice/v1/backups/{filename}` | Download, restore, or delete a backup |

The import request contains `filename`, base64-encoded `content`, and `apply`. Use
`apply: false` for validation and preview, then repeat the unchanged file with
`apply: true` after confirmation.

Document requests specify `document_type` (`payment_reminder`, `dunning_1`,
`dunning_2`, or `dunning_3`) and either `invoice_numbers` or
`invoice_number_from`/`invoice_number_to`. A request may instead provide
`accounting_task_batch_id` to use the exact still-open invoice membership of an
automatically grouped task. Only open receivables are eligible.

## Recurring and accounting tasks

Routines can define their task title, description, priority, and estimated duration
directly, without requiring a separate topic. Legacy topic assignments remain readable
for alpha-data migration. Routines support daily, weekly, monthly, yearly, and explicit-
date schedules together with due time, timezone, start and end dates, reminder offsets,
catch-up policy, invalid-month-day behavior, and an optional business-day adjustment.

`non_working_dates` adds company holidays or other exceptional closure dates to the
business-day calculation. Reminder catch-up can use the globally configured window,
send only the latest missed reminder, or skip reminders missed outside the scheduler
interval. Successful deliveries are committed to SQLite for restart-safe deduplication.

The server materializes concrete routine tasks from these definitions. Topic and step
content is snapshotted into each task so historical tasks remain understandable after a
template is edited.

Accounting rules are evaluated on the server. Each enabled stage defines the number of
days after the invoice due date, evaluation time, minimum open count, notification
choice, and automatic completion behavior. Matching receivables are grouped by original
due date and currency. For example, if 40 invoices share a due date and ten remain open,
the server creates one task linked to those ten invoice IDs, not ten separate tasks.

## Live editing contract

Every live record is returned in an envelope containing `id`, `collection`, `data`,
`revision`, `created_at`, `updated_at`, and `archived_at`. Updates send only the changed
fields together with `expected_revision`. A current revision is committed immediately.
If the revision is stale but all intervening events changed different fields, the server
merges the patch transactionally. If any field overlaps, the API responds with HTTP 409
and includes the current server record.

Clients subscribe with the Home Assistant WebSocket command
`structuraloffice/subscribe_live`. Events contain collection, record ID, operation,
revision, sequence, and changed field names, but no business payload. After reconnecting,
the client requests `/api/structuraloffice/v1/events?after=<sequence>` before resuming its
live subscription.

Edit presence is advisory rather than an exclusive lock. A client starts a session when
a record is opened, refreshes it before expiry, and ends it when the editor closes. This
allows other users to see concurrent editors while revision checks remain the final data
integrity mechanism.

## Backup and privacy

Backups are stored in `/config/structuraloffice/backups`. Restore creates an additional
safety backup before replacing the live database, and every schema migration creates a
backup before modifying the existing database. Restore rejects a source database that
fails SQLite's integrity check. StructuralOffice does not transmit business data to a
dedicated cloud service. Home Assistant access controls still apply to every API and
panel request.

StructuralOffice is an organizational aid. It does not replace tax, legal, or
professional accounting advice.
