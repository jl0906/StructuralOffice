# StructuralOffice API

This document describes the alpha API contract implemented by StructuralOffice
`0.5.0-alpha`. The contract may change before `1.0.0`.

## Connection and authorization

The base URL is `/api/structuraloffice/v1`. Send a Home Assistant access token with
every request:

```http
Authorization: Bearer <home-assistant-access-token>
Content-Type: application/json
```

Home Assistant authentication is followed by a StructuralOffice role check. Viewers may
read records and events. Editors may also change business records and edit presence.
Administrators additionally manage roles, backups, and audit access.

## Record envelopes

Live records use this envelope:

```json
{
  "archived_at": null,
  "collection": "contacts",
  "created_at": "2026-08-16T18:00:00+00:00",
  "data": {
    "id": "9ab123",
    "name": "Example Company"
  },
  "id": "9ab123",
  "revision": 3,
  "updated_at": "2026-08-16T18:04:00+00:00"
}
```

Supported collections are `contacts`, `topics`, `routines`, `occurrences`, and
`invoices`.

## List and create records

```http
GET /api/structuraloffice/v1/live/contacts?limit=100&offset=0
```

Set `include_archived=true` to include archived records. The response contains `items`,
`limit`, `offset`, and `total`.

```http
POST /api/structuraloffice/v1/live/contacts

{
  "data": {
    "name": "Example Company",
    "customer_number": "00042"
  }
}
```

Creation returns HTTP 201 and revision 1.

## Read and update a record

```http
GET /api/structuraloffice/v1/live/contacts/9ab123
```

Send changed fields only when updating:

```http
PATCH /api/structuraloffice/v1/live/contacts/9ab123

{
  "expected_revision": 3,
  "data": {
    "phone": "+49 123 456789"
  }
}
```

If another client changed only unrelated fields since revision 3, the server merges the
patch and returns operation `merged`. If an intervening change overlaps `phone`, the
response is HTTP 409 and includes the current server record.

## Archive a record

```http
DELETE /api/structuraloffice/v1/live/contacts/9ab123?expected_revision=4
```

Business records are archived, not physically deleted. A topic cannot be archived while
an active routine references it.

## Edit presence

Start a session when an editor opens a record:

```http
POST /api/structuraloffice/v1/editing/invoices/csv_123

{
  "client_id": "office-desktop-1",
  "ttl_seconds": 60
}
```

The response includes `session_id`, `expires_at`, and all current `editors`. Refresh the
session by repeating the request with its `session_id`. End it with:

```http
DELETE /api/structuraloffice/v1/editing/invoices/csv_123?session_id=<session-id>
```

Sessions last between 15 and 300 seconds and do not grant an exclusive lock.

## Live events and reconnect recovery

Subscribe through the Home Assistant WebSocket connection:

```json
{
  "id": 20,
  "type": "structuraloffice/subscribe_live"
}
```

Each event provides `collection`, `record_id`, `operation`, `revision`, `sequence`, and
`changed_fields`. Presence events additionally provide `editors`. Events do not contain
record payloads.

Persist the latest sequence on the Windows client. After reconnecting, retrieve missed
events before restarting the subscription:

```http
GET /api/structuraloffice/v1/events?after=125&limit=200
```

Clients then reload only affected records.

## Invoice imports and documents

Preview or apply an invoice-list CSV file:

```http
POST /api/structuraloffice/v1/imports/invoice-list

{
  "filename": "invoice-list.csv",
  "content": "<base64-content>",
  "apply": false
}
```

Generate documents only after explicit user confirmation:

```http
POST /api/structuraloffice/v1/documents

{
  "document_type": "payment_reminder",
  "invoice_numbers": ["000123", "000124"]
}
```

The server never generates or sends payment reminders or dunning notices automatically.

## Administrative endpoints

- `GET /api/structuraloffice/v1/audit`
- `GET /api/structuraloffice/v1/roles`
- `PUT /api/structuraloffice/v1/roles`
- `GET|POST /api/structuraloffice/v1/backups`
- `GET|POST|DELETE /api/structuraloffice/v1/backups/{filename}`

Audit responses contain metadata and changed field names, not complete payloads.
