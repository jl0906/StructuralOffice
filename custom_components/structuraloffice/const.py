"""Constants for StructuralOffice."""

from datetime import timedelta

DOMAIN = "structuraloffice"
NAME = "StructuralOffice"

PLATFORMS = ["sensor", "calendar"]

CONF_NOTIFY_TARGETS = "notify_targets"
CONF_DEFAULT_REMINDER_TIME = "default_reminder_time"
CONF_CATCH_UP_HOURS = "catch_up_hours"
CONF_COMPANY_NAME = "company_name"
CONF_COMPANY_ADDRESS = "company_address"
CONF_COMPANY_EMAIL = "company_email"

DEFAULT_REMINDER_TIME = "09:00"
DEFAULT_CATCH_UP_HOURS = 24
DEFAULT_COMPANY_NAME = ""
DEFAULT_COMPANY_ADDRESS = ""
DEFAULT_COMPANY_EMAIL = ""

STORAGE_VERSION = 1
STORAGE_KEY_PREFIX = "structuraloffice"
UPDATE_EVENT = "structuraloffice_updated"
SCHEDULER_INTERVAL = timedelta(minutes=1)

PANEL_URL = "structuraloffice"
PANEL_TITLE = "StructuralOffice"
PANEL_ICON = "mdi:office-building-cog"
PANEL_COMPONENT = "structuraloffice-panel"
FRONTEND_URL = "/structuraloffice_static/structuraloffice-panel.js"

STATUS_OPEN = "open"
STATUS_COMPLETED = "completed"
STATUS_SKIPPED = "skipped"
VALID_STATUSES = {STATUS_OPEN, STATUS_COMPLETED, STATUS_SKIPPED}
