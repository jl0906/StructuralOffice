"""Constants for StructuralOffice."""

from datetime import timedelta

DOMAIN = "structuraloffice"
NAME = "StructuralOffice"

PLATFORMS = ["sensor"]

CONF_NOTIFY_TARGETS = "notify_targets"
CONF_DEFAULT_REMINDER_TIME = "default_reminder_time"
CONF_DEFAULT_PAYMENT_TERM_DAYS = "default_payment_term_days"
CONF_SEPA_DATE_AS_DUE_DATE = "sepa_date_as_due_date"
CONF_CATCH_UP_HOURS = "catch_up_hours"
CONF_COMPANY_NAME = "company_name"
CONF_COMPANY_ADDRESS = "company_address"
CONF_COMPANY_EMAIL = "company_email"

DEFAULT_REMINDER_TIME = "09:00"
DEFAULT_PAYMENT_TERM_DAYS = 14
DEFAULT_SEPA_DATE_AS_DUE_DATE = True
DEFAULT_CATCH_UP_HOURS = 24
DEFAULT_COMPANY_NAME = ""
DEFAULT_COMPANY_ADDRESS = ""
DEFAULT_COMPANY_EMAIL = ""

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "structuraloffice"
DATABASE_SCHEMA_VERSION = 4
DATABASE_DIRECTORY = "structuraloffice"
DATABASE_FILENAME = "structuraloffice.db"
BACKUP_DIRECTORY = "backups"
UPDATE_EVENT = "structuraloffice_updated"
LIVE_UPDATE_EVENT = "structuraloffice_live_update"
SCHEDULER_INTERVAL = timedelta(minutes=1)

PANEL_URL = "structuraloffice"
PANEL_TITLE = "StructuralOffice"
PANEL_ICON = "mdi:office-building-cog"
PANEL_COMPONENT = "structuraloffice-panel"
FRONTEND_URL = "/structuraloffice_static/structuraloffice-panel.js"

STATUS_OPEN = "open"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
STATUS_CANCELLED = "cancelled"
STATUS_AUTO_COMPLETED = "auto_completed"
VALID_STATUSES = {
    STATUS_AUTO_COMPLETED,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_IN_PROGRESS,
    STATUS_OPEN,
    STATUS_SKIPPED,
}
