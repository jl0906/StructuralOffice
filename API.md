# StructuralOffice API

This document describes the stable API contract implemented by StructuralOffice
`1.0.0`. A machine-readable contract is
available in [OPENAPI.yaml](OPENAPI.yaml).

## Connection and authorization

The base URL is `/api/structuraloffice/v1`. Send a Home Assistant access token with
every request:

```http
Authorization: Bearer <home-assistant-access-token>
Content-Type: application/json
```

Home Assistant authentication is followed by a StructuralOffice role check. Viewers may
read records and events. Editors may also change business records.
Administrators additionally manage roles, backups, and audit access.

Every successful request is routed by the authenticated Home Assistant user ID to that
user's private SQLite database. Roles grant access to StructuralOffice itself; they never
grant access to another user's records or backups. WebSocket live events are filtered by
the same user identity. To add a person, first create a Home Assistant account and then
assign that account the StructuralOffice `viewer` or `editor` role.

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

## Live events and reconnect recovery

Subscribe through the Home Assistant WebSocket connection:

```json
{
  "id": 20,
  "type": "structuraloffice/subscribe_live"
}
```

Each event provides `collection`, `record_id`, `operation`, `revision`, `sequence`, and
`changed_fields`. Events do not contain record payloads.

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

Applying a source whose complete SHA-256 checksum is already present succeeds as a
no-op. Preview and apply responses include `known_rows`, `new_rows`, and `unchanged`.
Row fingerprints also detect bookings already seen in a different export.

## Materialized workflow tasks

Today's total estimated office workload and the longest open due task are available at:

```http
GET /api/structuraloffice/v1/dashboard/today
```

The total includes open and in-progress tasks due today or earlier. Future, completed,
skipped, and cancelled tasks are excluded.

Routines can create tasks directly without a topic. Create them through the revisioned
`routines` live collection, for example:

```http
POST /api/structuraloffice/v1/live/routines

{
  "data": {
    "name": "Report employee vacation days to the tax adviser",
    "description": "",
    "estimated_minutes": 15,
    "priority": "normal",
    "due_time": "09:00",
    "timezone": "Europe/Berlin",
    "enabled": true,
    "topic_ids": [],
    "reminder_offsets": [],
    "schedule": {
      "frequency": "monthly",
      "interval": 1,
      "start_date": "2026-08-01",
      "month_days": [8]
    }
  }
}
```

List persisted routine and accounting tasks:

```http
GET /api/structuraloffice/v1/tasks?status=open&source_type=routine&limit=100&offset=0
```

`source_type` is `routine` for recurring work, `accounting_due_batch` for automatic
invoice follow-up tasks, and `manual` for standalone work. Routine tasks snapshot their
topic metadata and checklist when materialized. Recurrence definitions support one-time,
daily, weekly, monthly, and yearly schedules, multiple weekdays or month days, explicit
dates, intervals, start and end dates, reminder offsets, catch-up policy, invalid-date
handling, business-day adjustment, and explicit non-working dates.

Create and update standalone or materialized tasks:

```http
POST /api/structuraloffice/v1/tasks

{
  "title": "Prepare quarterly report",
  "due_at": "2026-10-01T09:00:00+02:00",
  "priority": "high",
  "estimated_minutes": 45,
  "checklist": ["Collect figures", "Review report"]
}
```

```http
PATCH /api/structuraloffice/v1/tasks/<task-id>

{
  "expected_revision": 1,
  "data": {
    "status": "in_progress",
    "completion_note": "Preparation started"
  }
}
```

Mutable fields for every task are `status`, `priority`, `due_at`, `estimated_minutes`,
and `completion_note`. Manual tasks additionally accept `title`, `description`,
`category`, and an atomic replacement `checklist`. Generated task source text and
checklist structure stay linked to their workflow. Generated checklist items are
independently revisioned:

```http
PATCH /api/structuraloffice/v1/tasks/<task-id>/checklist/<item-id>

{
  "expected_revision": 1,
  "data": {
    "completed": true,
    "note": "Verified against the ledger"
  }
}
```

Task and checklist writes emit persistent change events and audit metadata. Stale writes
return HTTP 409 with the current task or checklist item.

Cancel several active tasks in one transaction:

```http
POST /api/structuraloffice/v1/tasks/bulk-cancel

{
  "tasks": [
    {"id": "task-a", "expected_revision": 2},
    {"id": "task-b", "expected_revision": 5}
  ]
}
```

The request accepts 1 to 500 tasks. If one task is missing, inactive, or stale, no task
is changed. Cancellation keeps the audit history and exact accounting membership.

## Payment-reminder to dunning transition

Accounting tasks use explicit workflow transitions. A generic task patch cannot mark a
payment-reminder or dunning task as completed. The task snapshot exposes
`available_actions`, containing `schedule_dunning` or `confirm_settled` when the
corresponding frontend action is valid.

When the user completes a payment-reminder writing task, the frontend asks for the
payment deadline stated in that reminder and sends:

```http
POST /api/structuraloffice/v1/tasks/<task-id>/schedule-dunning

{
  "expected_revision": 3,
  "due_date": "2026-08-31"
}
```

The transaction completes the payment-reminder package and creates one linked dunning
package due at 09:00 on the selected date. Only
invoices still open at that moment are copied into the dunning task. Calling the
transition twice is rejected.

After a later CSV import shows that every linked invoice is no longer open, the dunning
task remains open and its snapshot contains `settlement_confirmation_required: true`
and `settlement_detected_at`. The frontend should ask for confirmation and then send:

```http
POST /api/structuraloffice/v1/tasks/<task-id>/confirm-settled

{
  "expected_revision": 5
}
```

The backend verifies that no linked invoice is open before completing the task. Both
transitions require editor access, use optimistic revisions, emit live events, and add
audit/change metadata.

## Reminder catch-up

Each routine chooses one policy:

- `configured_window` sends every missed reminder still inside the Home Assistant
  catch-up window.
- `latest_only` sends only the newest eligible reminder for each task.
- `skip_missed` accepts only reminders inside the normal scheduler interval.

Scheduling uses the routine's validated IANA timezone. Successful deliveries are stored
in SQLite, preventing duplicate sends after Home Assistant restarts.

## Import history

```http
GET /api/structuraloffice/v1/imports?limit=100&offset=0
GET /api/structuraloffice/v1/imports/<import-id>
GET /api/structuraloffice/v1/imports/<import-id>/source
```

The list contains source metadata and aggregate counts but not source contents. Details
include row fingerprints and invoice numbers. Only administrators may download the
retained original CSV source.

## Grouped accounting tasks

```http
GET /api/structuraloffice/v1/accounting/tasks?status=open
GET /api/structuraloffice/v1/accounting/tasks/<batch-id>/invoices
```

The first endpoint returns task summaries. The second returns exact invoice IDs and the
open/resolved state of every member. The server creates at most one batch per configured
rule, original invoice due date, and currency. Membership contains only receivables that
are still open when the batch is evaluated. Empty batches are automatically completed
when enabled by the rule.

Use the exact membership for explicitly requested document generation:

```http
POST /api/structuraloffice/v1/documents

{
  "document_type": "payment_reminder",
  "accounting_task_batch_id": "<batch-id>"
}
```

This creates files only for currently open members. It does not send them.

## Accounting task rules

```http
GET /api/structuraloffice/v1/accounting/rules
```

Default rules create a payment-reminder task one day after the due date and dunning
tasks after 14, 30, and 60 days. An editor or administrator can update a rule with
optimistic revision protection:

```http
PATCH /api/structuraloffice/v1/accounting/rules/payment-reminder-default

{
  "expected_revision": 1,
  "data": {
    "days_after_due": 3,
    "evaluation_time": "09:00",
    "minimum_open_invoices": 1,
    "notify_enabled": true,
    "enabled": true
  }
}
```

Mutable fields are `days_after_due`, `evaluation_time`, `minimum_open_invoices`,
`auto_complete_empty_batches`, `notify_enabled`, and `enabled`.

## Administrative endpoints

- `GET /api/structuraloffice/v1/audit`
- `GET /api/structuraloffice/v1/roles`
- `PUT /api/structuraloffice/v1/roles`
- `GET|POST /api/structuraloffice/v1/backups`
- `GET|POST|DELETE /api/structuraloffice/v1/backups/{filename}`

Audit responses contain metadata and changed field names, not complete payloads.
