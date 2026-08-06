# bench — die Apparatur, die den Strategietext falsifizierbar macht

Zwei Dateien und ein Vertrag:

- **`tasks_v1.json`** — die eingefrorene Aufgabenliste. Reine Aufgabenzeilen;
  welche Aufgabe für welche Falle gedacht ist, steht in `tasks_v1_intent.json`
  und wird der prüfenden Seite **erst nach ihrer Zählung** gezeigt.
- **`score.py`** — die Auswertung. Eine **reine Funktion über das Protokoll**
  der Jetson-Seite (`loop_proof.log.jsonl`), nie über die lebende Brücke: damit
  ist sie ohne Gast prüfbar, man kann ihr eine erfundene Spur unterschieben, und
  ein Loch in der Spur ist auffindbar, statt still zu einer wohlgeformten Null
  zu werden.

Der Protokollvertrag ist `schema: 2`, ausgehandelt am 2026-08-06. `score.py`
**lehnt `schema: 1` ab** — jene Fassung hatte zwei Löcher (der
Wiederholungsvermerk stand nicht in der Datei, und verweigerte Aufrufe erzeugten
gar keinen `tool`-Satz), und eine Spur mit einem Loch darf nicht stillschweigend
mitzählen.

Jede Kennzahl trägt dasselbe Etikett: **gemessen auf MPW `SC`, überträgt sich
nicht auf THINK C.**

Hintergrund und die Begründung jeder Entwurfsentscheidung:
<https://pit.390er.de/nvidia/apparatur-vor-der-zahl-bauplan-erstversuchsquote/>
