# Security Policy

## Supported versions

StructuralOffice is currently in beta. Security fixes are provided only for the most
recent published build.

## Reporting a vulnerability

Use private vulnerability reporting under **Security → Report a vulnerability** in the
GitHub repository. Do not publish sensitive details in a public issue.

Include the affected version, expected and actual behavior, potential impact, and
reproduction steps when possible. Never submit Home Assistant configuration files,
access tokens, or diagnostic archives containing personal or secret data.

## Local data and API security

StructuralOffice stores each Home Assistant user's business data below
`/config/structuraloffice/users/<opaque-user-id>/`, with a separate `structuraloffice.db`
and `backups` directory for every user. Protect the complete StructuralOffice directory
with the same care as the Home Assistant configuration directory. The former shared
database is retained after the first `0.9.2-beta` migration for rollback and must receive
the same protection. The versioned REST API requires Home Assistant authentication and
applies StructuralOffice roles. Never embed a long-lived access token in source code,
logs, support archives, or public reports.

Database routing uses the authenticated Home Assistant user ID, not a client-provided
identifier. Removing a role denies access without deleting data. WebSocket events,
backups, imports, audit logs, and edit-presence sessions use the same per-user boundary.

Live change notifications deliberately contain record identifiers, revision numbers,
operations, and changed field names only. Business payloads remain behind authenticated
REST endpoints. Edit-presence sessions expire automatically and are advisory; revision
checks and SQLite transactions enforce data integrity.

Invoice-import metadata follows normal viewer permissions, while downloading a retained
original CSV source requires the StructuralOffice administrator role. Task, checklist,
and accounting-rule writes require editor access and an expected revision. Generated
payment-reminder and dunning documents are returned only after an explicit authenticated
request and are never sent automatically.
