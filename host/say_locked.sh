#!/bin/bash
# say_locked.sh WHO MESSAGE — every spoken AppleBridge alert goes through here (2026-10-24), logged with who spoke and when.
# 2026-10-25, the sound manager (operator: "einen Sound Manager ..., der die Meldungen sequenziell ausgibt oder in einer
# Meldung zusammenfasst", after two voices overlapped again): a caller only QUEUES its message (one file in the spool) and
# then tries to become the one speaker. The speaker waits MERGE_S for more, takes everything queued, says identical texts
# once, several messages as one announcement ("Zwei Meldungen. Erstens: ... Zweitens: ..."), and repeats until the spool
# is empty. Nothing is lost on the hand-over: a caller writes before it tries the lock, and the speaker looks at the spool
# again after releasing it. The interface is unchanged, so every caller (watchers, chains, ssh from the other machines)
# keeps working without an edit.
SPOOL=${APPLEBRIDGE_SAY_SPOOL:-/tmp/applebridge_say.spool}; LOG=${APPLEBRIDGE_SAY_LOG:-/tmp/applebridge_say.log}; LOCK=${APPLEBRIDGE_SAY_LOCK:-/tmp/applebridge_say.lock}
MERGE_S=${APPLEBRIDGE_SAY_MERGE_S:-3}
mkdir -p "$SPOOL"
if [ "$1" != "--drain" ]; then
    echo "$(date '+%F %T') [$1] wait: $2" >> "$LOG"
    f=$(mktemp "$SPOOL/q.$(date +%s).XXXXXX") && printf '%s\t%s' "$1" "$2" > "$f.tmp" && mv "$f.tmp" "$f.msg" && rm -f "$f"
fi
# become the speaker if nobody is; whoever holds the lock will see our file
while ls "$SPOOL"/*.msg >/dev/null 2>&1; do
    /usr/bin/lockf -s -t 0 "$LOCK" /usr/bin/python3 - "$SPOOL" "$LOG" "$MERGE_S" <<'EOF' || break
import glob, os, subprocess, sys, time
spool, log, merge = sys.argv[1], sys.argv[2], float(sys.argv[3])
NUM = {2: "Zwei", 3: "Drei", 4: "Vier", 5: "Fünf"}
ORD = ["Erstens", "Zweitens", "Drittens", "Viertens", "Fünftens"]
stamp = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
while True:
    time.sleep(merge)                                   # let messages that belong together arrive
    files = sorted(glob.glob(os.path.join(spool, "*.msg")))
    if not files: break
    msgs = []
    for f in files:
        try: who, _, text = open(f, encoding="utf-8").read().partition("\t")
        except OSError: continue
        os.remove(f); msgs.append((who, text.strip()))
    uniq = []
    for who, text in msgs:
        hit = next((u for u in uniq if u[1] == text), None)
        if hit: hit[0].append(who)
        else: uniq.append([[who], text])
    if len(uniq) == 1: speech = uniq[0][1]
    else:
        n = len(uniq)
        speech = f"{NUM.get(n, 'Mehrere')} Meldungen. " + " ".join(f"{ORD[i] if i < len(ORD) else 'Dann'}: {t}" for i, (_, t) in enumerate(uniq))
    whos = ",".join(w for ws, _ in uniq for w in ws)
    with open(log, "a") as L: L.write(f"{stamp()} [{whos}] SAY ({len(msgs)} queued, {len(uniq)} distinct): {speech}\n")
    if os.environ.get("APPLEBRIDGE_SAY_DRY"): time.sleep(2)   # tests: the time of a short sentence, no sound
    else: subprocess.run(["say", "-v", "Anna", speech])
    with open(log, "a") as L: L.write(f"{stamp()} [{whos}] done\n")
EOF
done
exit 0
