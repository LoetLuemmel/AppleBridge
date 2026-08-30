# Rückmeldung an AppleBridge — 2026-08-08

Von der Xavier-Sitzung (`apfelpilot-live`). Vier Befunde eines Tages, alle mit
Beleg, alle beim Arbeiten an etwas anderem aufgefallen. **Neue, unversionierte
Datei — kein bestehender Text ist angefasst, nichts ist committet.** Verschieben,
einarbeiten oder löschen nach eurem Ermessen.

---

## 1. `host/mpw.py` — die Remedy feuert bei der real auftretenden Meldung nicht

`_REMEDIES` erkennt die fehlende TEXT-Type-Klasse an `-31001` oder
`Not a text file`:

```python
(re.compile(r"-31001|Not a text file", re.I),
 "The file has no TEXT type, so MPW will not open it — this is the usual "
 "result of Duplicate out of Unix:. Fix with: SetFile -t TEXT -c 'MPS ' <file>"),
```

**SC meldet in diesem Fall aber `Command line error: unable to open input file`.**
Die Zeichenfolge `unable to open input` kommt im ganzen Repository nicht vor
(geprüft mit `grep -rni "unable to open input" --include=*.py --include=*.md
--include=*.c .`). Die M4Pro-Sitzung hat deshalb 120 Fehlschläge bekommen, ohne
den Hinweis zu sehen, der seit langem im Projekt steht.

Vorschlag:

```python
(re.compile(r"-31001|Not a text file|unable to open input file", re.I), …)
```

Der Wert dieser Zeile ist nicht die Regex, sondern dass das Projektwissen dann
**in dem Moment ankommt, in dem es gilt** — was ja der erklärte Zweck von
`_REMEDIES` ist.

---

## 2. ExtFS (`Unix:`) taugt nicht als Quelle für MPW-Werkzeuge — gemessen

| Versuch | Ergebnis |
|---|---|
| `SC 'Unix:<ordner>:<datei>.c'` | scheitert bei **allen** Dateien: `unable to open input file` |
| `SetFile -t TEXT -c 'MPS '` auf der ExtFS-Datei, dann `SC` | **unverändert derselbe Fehler** |
| `LISTDIR 'Unix:<ordner>:'`, `mac_read_file` | funktionieren einwandfrei |

MPW öffnet nach **TYPE**, nicht nach Endung; ExtFS trägt keinen TEXT-Typ, und die
Finder-Info wird dort synthetisiert statt zurückgeschrieben — deshalb hält
`SetFile` nicht.

**Der wichtige Nebensatz für die Dokumentation:** dass `LISTDIR` und
`mac_read_file` denselben Pfad lesen können, erlaubt **keinen** Schluss darauf,
dass ein MPW-Werkzeug ihn öffnen kann. Das sind zwei Zugriffswege. Diesen
Fehlschluss habe ich selbst gemacht, die M4Pro-Sitzung hat ihn korrigiert.

Tragfähiger Weg, an einem Lauf über 120 Dateien bestätigt:
`mac_read_file` von `Unix:` → `mac_write_file` nach `MeinMac:` → `mac_compile`.
Kein `Duplicate`, kein `SetFile`, keine Annahme über ExtFS.

---

## 3. `Duplicate` aus `Unix:` heraus hat die Brücke stillgelegt

Aus `/tmp/applebridge_host_launchd.log`:

```
[12:41:14] LISTDIR 'Unix:basiszahl_120:roh:'   -> recv 5502B outcome=framed
[12:41:14] cmd: 'Duplicate "Unix:basiszahl_120:roh:99ef4ad5e5d55973.c" ...'
[12:41:36] LISTDIR 'Unix:basiszahl_120:roh:'   -> recv 5502B
[12:41:36] cmd: 'Duplicate "Unix:basiszahl_120:roh:99ef4ad5e5d55973.c" ...'
[12:45:36] command timeout after 240s: 'Duplicate "Unix:..."'
[12:45:36] control error: [Errno 32] Broken pipe
```

Dieselbe Datei — rund **5 kB** — mehrfach, dann 240 s Zeitüberschreitung, danach
war die Verbindung weg. Der Herzschlag ist dabei **Folge, nicht Ursache**: im
ganzen Tageslog stehen drei `heartbeat missed`, einer davon um 12:45:50, also
*nach* dem Hänger.

Bemerkenswert ist die Verwechslungsgefahr: das Fehlerbild sah nach Überlastung
aus (rund 240 Apple-Event-Rundreisen zuvor), war aber keine — **alle** Duplicates
scheiterten, nicht die späteren. „Alle scheitern" gegen „die späteren scheitern"
ist der Unterschied zwischen systematischer Ursache und Erschöpfung, und er ist
im Log ablesbar.

Vorschlag für TROUBLESHOOTING: `Duplicate` über die `Unix:`-Grenze als bekannt
unzuverlässig führen, mit dem Verweis auf den Weg aus Punkt 2.

---

## 4. Ein Bildmodell ersetzt kein Auslesen — gemessen am „Compile Errors"-Fenster

Auftrag der AppleBridge-Sitzung: `gatecal/thinkc_error_02.png` (1024×768) mit dem
Orin-Weg lesen. Apparat: `gemma3:4b` über Ollama, `temperature 0`, je zweimal.

| Bedingung | Ausgabe (beide Läufe zeichengleich) | Zeit |
|---|---|---|
| Vollbild | `NO COMPILE ERRORS WINDOW` | 6,4 / 1,3 s |
| Fenster ausgeschnitten, 3× | `File main.c; Line 63` | 6,8 / 1,6 s |
| nur die zwei Textzeilen, 5× | `"file index.html"` | 6,4 / 1,1 s |

Tatsächlicher Inhalt (eigene Sicht, unabhängig belegt durch die Zeilennummer):

```
Compile Errors
File "main.c"; Line 63
Error:   invalid redeclaration of '_doprnt'
```

Drei Beobachtungen, in der Reihenfolge ihrer Wichtigkeit:

1. **Es schweigt nicht, wenn es scheitert.** `"file index.html"` kam zweimal, mit
   derselben Bestimmtheit wie das richtige `Line 63`. Ein Leser, dessen Irrtümer
   sich nicht von seinen Treffern unterscheiden lassen, ist in einer Messkette
   gefährlicher als gar kein Leser.
2. **Am Vollbild schlägt es nie an** — auch nicht, wenn ein Fenster da ist. Eine
   Gegenprobe „meldet er nichts, wenn nichts da ist?" besteht es deshalb nur
   scheinbar.
3. **Mehr Vergrößerung machte es schlechter.** Die Interpolation zerstört
   Bitmap-Schrift, statt sie lesbar zu machen — die Intuition „größer hilft"
   trägt hier nicht.

**Vorschlag statt OCR:** „Compile Errors" ist ein **Textfenster**. Wenn der Daemon
seinen Inhalt als Text holen kann (TE-Handle des Fensters, analog zum
Menüleisten-Walk), ist die Meldung zeichengenau, sofort und kostenlos — und die
Zeilennummer kommt als Zahl an, nicht als Vermutung. *Was man lesen kann, sollte
man nicht erraten.* Muss es doch über Pixel gehen, dann mit einem echten
OCR-Motor (Tesseract mit passendem Zeichensatz), nicht mit einem Sprachmodell;
das wäre eine Neuinstallation auf dem Orin, keine Reaktivierung.

---

## Kleinigkeit

Die Aufgabenstellung nannte die Bilddateien „auf diesem Mac (192.168.3.75)".
`192.168.3.75` ist der **Xavier**; die Dateien lagen auf `192.168.3.154`.

---

## Was diese vier Punkte verbindet

Drei von vier sind dieselbe Fehlerklasse: **ein leeres oder erfundenes Ergebnis
wurde als Befund gelesen.** Der fehlende Remedy-Treffer, die stumme
ExtFS-Ablehnung, das „NO COMPILE ERRORS WINDOW" bei vorhandenem Fenster — jedes
Mal sah das Nichts wie eine Antwort aus. Die Gegenmaßnahme ist billig und immer
dieselbe: **prüfen, was ankommt, nicht was gesendet wurde**, und ein Werkzeug, das
scheitert, laut scheitern lassen.
