# Security Policy

## Supported versions

StructuralOffice is currently in alpha. Security fixes are provided only for the most
recent published build.

## Reporting a vulnerability

Use private vulnerability reporting under **Security → Report a vulnerability** in the
GitHub repository. Do not publish sensitive details in a public issue.

Include the affected version, expected and actual behavior, potential impact, and
reproduction steps when possible. Never submit Home Assistant configuration files,
access tokens, or diagnostic archives containing personal or secret data.

## Local data and API security

StructuralOffice stores business data in `/config/structuraloffice/structuraloffice.db`
and managed copies in `/config/structuraloffice/backups`. Protect both locations with
the same care as the Home Assistant configuration directory. The versioned REST API
requires Home Assistant authentication and applies StructuralOffice roles. Never embed
a long-lived access token in source code, logs, support archives, or public reports.
