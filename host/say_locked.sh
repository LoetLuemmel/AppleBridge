#!/bin/bash
# say_locked.sh WHO MESSAGE (2026-10-24): every spoken AppleBridge alert, one at a time, logged with who spoke and when.
echo "$(date '+%F %T') [$1] wait: $2" >> /tmp/applebridge_say.log
/usr/bin/lockf -k /tmp/applebridge_say.lock /bin/bash -c 'echo "$(date "+%F %T") [$1] SAY: $2" >> /tmp/applebridge_say.log; say -v Anna "$2"; echo "$(date "+%F %T") [$1] done" >> /tmp/applebridge_say.log' _ "$1" "$2"
