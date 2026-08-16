# Changelog

Alle wesentlichen Änderungen an StructuralOffice werden in dieser Datei dokumentiert.
Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

Noch keine Änderungen.

## [0.3.0] – 2026-08-16

### Hinzugefügt

- Rollierende Buchhaltungsauswertung über zwölf Monate
- Auswertung der erledigten, offenen und übersprungenen Aufgaben
- Altersstruktur offener Forderungen nach Fälligkeit
- PDF-Zahlungserinnerungen sowie erste, zweite und dritte Mahnungen
- Firmendaten für automatisch erzeugte Mahndokumente
- Rollenverwaltung für Administratoren, Bearbeiter und Betrachter
- Neutraler, Excel-kompatibler CSV-Export
- Kontaktanschrift für Buchhaltungsdatensätze und Mahndokumente

### Geändert

- Das StructuralOffice-Panel ist für freigeschaltete Home-Assistant-Benutzer verfügbar.
- Schreibende Aktionen werden zusätzlich serverseitig anhand der Benutzerrolle geprüft.
- Das Frontend enthält einen eigenen Bereich für Auswertungen und Rollenverwaltung.
- Die Frontend-Cache-Version wurde auf `0.3.0` angehoben.

### Sicherheit

- Nicht freigeschaltete Benutzer erhalten keinen Zugriff auf StructuralOffice-Daten.
- Betrachter dürfen Daten lesen und exportieren, aber nicht verändern.
- Nur Administratoren dürfen Rollen verwalten und Testbenachrichtigungen auslösen.

## [0.2.0] – 2026-08-16

### Hinzugefügt

- Verwaltung von Eingangs- und Ausgangsrechnungen direkt im Panel
- Netto-, Steuer- und Bruttobeträge sowie Zahlungsstatus und Mahnstufen
- Erinnerungen für fällige Zahlungen und mehrstufige Mahnfristen
- Validierte Excel-Vorlage mit Importvorschau
- Excel-Import und Excel-Export mit stabilen Datensatz-IDs
- Buchhaltungskennzahlen und zusätzliche Home-Assistant-Sensoren

## [0.1.0] – 2026-08-16

### Hinzugefügt

- Erste HACS-kompatible Version der StructuralOffice-Integration
- Eigenes Home-Assistant-Panel
- Wiederverwendbare Topics mit Beschreibung, Kategorie und Checkliste
- Einmalige, tägliche, wöchentliche, monatliche und jährliche Routinen
- Mehrere Fälligkeitstage und Erinnerungsabstände pro Routine
- Pushbenachrichtigungen an ausgewählte `notify`-Entitäten
- Aufgabenstatus, Sensoren, Kalender und lokale Speicherung

[Unreleased]: https://github.com/jl0906/StructuralOffice/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/jl0906/StructuralOffice/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/jl0906/StructuralOffice/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jl0906/StructuralOffice/releases/tag/v0.1.0
