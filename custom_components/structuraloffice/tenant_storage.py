"""Pure filesystem helpers for isolated StructuralOffice user databases."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

from .const import BACKUP_DIRECTORY, DATABASE_FILENAME
from .models import StructuralOfficeValidationError

TENANT_DIRECTORY = "users"


def tenant_directory_name(user_id: str) -> str:
    """Return a stable opaque directory name for one Home Assistant user."""
    if not user_id:
        raise StructuralOfficeValidationError("A Home Assistant user ID is required")
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def tenant_database_paths(root: Path, user_id: str) -> tuple[Path, Path]:
    """Return the database and backup paths owned by one user."""
    tenant_root = root / TENANT_DIRECTORY / tenant_directory_name(user_id)
    return tenant_root / DATABASE_FILENAME, tenant_root / BACKUP_DIRECTORY


def migrate_legacy_database(
    legacy_database: Path,
    legacy_backups: Path,
    tenant_database: Path,
    tenant_backups: Path,
) -> bool:
    """Copy the former shared database and backups into the owner's tenant.

    The source is intentionally retained so an upgrade can always be rolled back.
    SQLite's backup API also includes committed WAL contents in the destination.
    """
    if not legacy_database.exists() or tenant_database.exists():
        return False

    tenant_database.parent.mkdir(parents=True, exist_ok=True)
    tenant_backups.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(legacy_database) as source, sqlite3.connect(tenant_database) as target:
        source.backup(target)

    if legacy_backups.exists():
        for backup in legacy_backups.glob("*.db"):
            destination = tenant_backups / backup.name
            if not destination.exists():
                shutil.copy2(backup, destination)
    return True
