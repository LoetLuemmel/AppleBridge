#!/bin/bash
#
# AppleBridge host server launcher.
#
# Starts host_server.py with the FIREWALL-ALLOWLISTED system Python
# (/usr/bin/python3), NOT the project venv python. The venv binary is a
# different, un-allowlisted executable, so inbound connections from the Mac
# daemon (across the bridge, e.g. 192.168.3.244 -> 192.168.3.154:9000) get
# silently blocked by the macOS application firewall. host_server.py is
# stdlib-only, so the system Python is sufficient.
#
# Usage:
#   ./run_server.sh              # foreground
#   nohup ./run_server.sh &      # background, logs to /tmp/applebridge_server.log
#
cd "$(dirname "$0")" || exit 1
exec /usr/bin/python3 -u host_server.py
