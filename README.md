# StructuralOffice

StructuralOffice ist eine lokale Home-Assistant-Custom-Integration für wiederkehrende betriebliche Abläufe. Topics bilden wiederverwendbare Aufgaben ab; Routinen verbinden diese Topics mit Fälligkeitstagen und Push-Erinnerungen.

## Funktionsumfang von Version 0.3

- Eigenes, responsives StructuralOffice-Panel in der Home-Assistant-Seitenleiste
- Topics mit Beschreibung, Kategorie und Checkliste
- Einmalige, tägliche, wöchentliche, monatliche und jährliche Routinen
- Mehrere Topics, Fälligkeitstage und Erinnerungsabstände pro Routine
- Status je erzeugter Aufgabe: offen, erledigt oder übersprungen
- Pushbenachrichtigungen an ausgewählte `notify`-Entitäten
- Nachholen verpasster Erinnerungen nach einem Home-Assistant-Ausfall
- Drei native Aufgabensensoren und ein Fälligkeitskalender
- Lokale, versionierte Speicherung über Home Assistants Storage-System
- Deutsche und englische Einrichtungsdialoge
- Eingangs- und Ausgangsrechnungen mit Netto-, Steuer- und Bruttobetrag
- Zahlungsstatus, Fälligkeitsdatum, Bezahldatum und Mahnstufe
- Individuelle Zahlungserinnerungen und mehrstufige Mahnfristen
- Buchhaltungskennzahlen im Dashboard und vier zusätzliche Sensoren
- Bearbeitung direkt im StructuralOffice-Panel
- Validierte Excel-Vorlage, Importvorschau und Excel-Export
- Neutraler CSV-Export als Schnittstelle zu externen Buchhaltungssystemen
- Rollierende 12-Monats-Auswertung, Erledigungsquote und Forderungsalter
- Zahlungserinnerungen sowie drei Mahnstufen als direkt erzeugte PDF-Dokumente
- Rollen für Home-Assistant-Benutzer: Administrator, Bearbeiter und Betrachter

StructuralOffice unterstützt die Organisation buchhalterischer Abläufe, ersetzt aber keine steuerliche oder rechtliche Beratung.

## Installation über HACS

Bis das Repository in der HACS-Standardliste enthalten ist:

1. Dieses Repository in HACS als benutzerdefiniertes Repository vom Typ **Integration** hinzufügen.
2. **StructuralOffice** installieren.
3. Home Assistant neu starten.
4. Unter **Einstellungen → Geräte & Dienste → Integration hinzufügen** nach StructuralOffice suchen.
5. Mindestens eine `notify`-Entität auswählen.

Danach erscheint StructuralOffice in der Seitenleiste. Home-Assistant-Administratoren haben automatisch Vollzugriff und können im Panel weitere Benutzer als Bearbeiter oder Betrachter freischalten.

## Routinen

Erinnerungsabstände werden relativ zum Fälligkeitstag angegeben:

- `-7`: sieben Tage vorher
- `-1`: einen Tag vorher
- `0`: am Fälligkeitstag
- `3`: drei Tage danach

Monatliche Routinen können mehrere Monatstage enthalten. Einmalige Routinen können mehrere konkrete ISO-Daten (`JJJJ-MM-TT`) enthalten.

## Buchhaltung und Excel

Im Bereich **Buchhaltung** können Eingangs- und Ausgangsrechnungen direkt gepflegt werden. Eingangsrechnungen erzeugen Zahlungserinnerungen; bei offenen Ausgangsrechnungen können zusätzliche Mahnstufen beispielsweise 3, 10 und 20 Tage nach Fälligkeit ausgelöst werden.

Über **Vorlage** wird eine bearbeitbare `.xlsx`-Datei heruntergeladen. Beim Import prüft StructuralOffice zunächst alle Zeilen und zeigt neue Datensätze, Aktualisierungen, Warnungen und Fehler an. Erst nach einer ausdrücklichen Bestätigung werden gültige Datensätze gespeichert.

Die Excel-Datei ist ein Austauschformat. StructuralOffice bleibt die führende Datenquelle. Beim Export bleiben die internen IDs erhalten, sodass bearbeitete Zeilen beim nächsten Import aktualisiert werden können.

Der CSV-Export ist bewusst neutral gehalten. Für eine konkrete Buchhaltungssoftware kann darauf ein eigener Adapter aufbauen, ohne die interne Datenhaltung zu verändern.

## Mahndokumente und Rollen

Für offene Ausgangsrechnungen erzeugt StructuralOffice Zahlungserinnerungen sowie erste, zweite und dritte Mahnungen als PDF. Firmenname, Anschrift und E-Mail werden im Optionsdialog der Integration gepflegt. Die erzeugten Schreiben sind Vorlagen und müssen vor dem Versand fachlich sowie rechtlich geprüft werden.

- **Administrator:** Einstellungen, Benutzerrollen und alle Daten
- **Bearbeiter:** Topics, Routinen, Aufgaben und Buchhaltung bearbeiten; Mahn-PDFs erzeugen
- **Betrachter:** Daten und Auswertungen lesen; Excel und CSV exportieren

## Datenschutz

StructuralOffice überträgt keine Daten an einen eigenen Cloud-Dienst. Topics, Routinen, Aufgabenstatus und der Benachrichtigungsverlauf liegen im lokalen Home-Assistant-Speicher und werden von Home-Assistant-Backups erfasst.
