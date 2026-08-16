# StructuralOffice

StructuralOffice is a local Home Assistant backend for recurring business processes and
invoice due-date monitoring. The operational desktop interface is being moved to a
separate Windows application. Home Assistant now provides database health statistics
and managed backup controls only.

## Version 0.4.0-alpha

This release introduces the backend boundary planned for the Windows client:

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
- Database-size, record-count, backup-count, and import-count sensors

StructuralOffice never creates payment reminders or dunning notices automatically.
The backend only calculates invoice due states. A document is generated only after an
authorized user explicitly requests it, and it must be reviewed before sending.

## Installation with HACS

Until the repository is included in the default HACS repository list:

1. Add this repository to HACS as a custom repository of type **Integration**.
2. Install **StructuralOffice**.
3. Restart Home Assistant.
4. Open **Settings → Devices & services → Add integration** and select StructuralOffice.
5. Configure the default payment term and whether a SEPA debit date overrides it.

The StructuralOffice sidebar panel is restricted to Home Assistant administrators. It
contains backend statistics and backup management. Operational topics, routines,
invoice imports, invoice lists, and document workflows are intended for the Windows
application.

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

## REST API for the Windows client

The API uses Home Assistant authentication. Clients send a valid Home Assistant bearer
token in the `Authorization` header. Authorization is additionally limited by the
StructuralOffice administrator, editor, and viewer roles.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/structuraloffice/v1/status` | Backend and database status |
| `GET` | `/api/structuraloffice/v1/invoices` | Normalized invoices; supports `status` and `due_state` filters |
| `POST` | `/api/structuraloffice/v1/imports/invoice-list` | Preview or apply a base64-encoded CSV import |
| `POST` | `/api/structuraloffice/v1/documents` | Explicitly generate one PDF or a ZIP batch |
| `GET/POST` | `/api/structuraloffice/v1/backups` | List or create backups |
| `GET/POST/DELETE` | `/api/structuraloffice/v1/backups/{filename}` | Download, restore, or delete a backup |

The import request contains `filename`, base64-encoded `content`, and `apply`. Use
`apply: false` for validation and preview, then repeat the unchanged file with
`apply: true` after confirmation.

Document requests specify `document_type` (`payment_reminder`, `dunning_1`,
`dunning_2`, or `dunning_3`) and either `invoice_numbers` or
`invoice_number_from`/`invoice_number_to`. Only open receivables are eligible.

## Backup and privacy

Backups are stored in `/config/structuraloffice/backups`. Restore creates an additional
safety backup before replacing the live database and rejects a source database that
fails SQLite's integrity check. StructuralOffice does not transmit business data to a
dedicated cloud service. Home Assistant access controls still apply to every API and
panel request.

StructuralOffice is an organizational aid. It does not replace tax, legal, or
professional accounting advice.
