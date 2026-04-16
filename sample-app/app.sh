#!/bin/sh
# Sample Docksmith application — pure POSIX shell, runs on the Alpine base image.
#
# ENV vars (overridable with `-e KEY=value` at `docksmith run`):
#   GREETING    — greeting word        (default: Hello)
#   TARGET      — who to greet         (default: Docksmith)
#   APP_VERSION — set via Docksmithfile ENV
#   EMPHASIS    — banner accent color  (default: cyan)

set -eu

GREETING="${GREETING:-Hello}"
TARGET="${TARGET:-Docksmith}"
APP_VERSION="${APP_VERSION:-unknown}"
EMPHASIS="${EMPHASIS:-cyan}"

# Source the vendored color helpers
. /app/vendor/colorize.sh

BAR='════════════════════════════════════════════════════════════'
DASH='────────────────────────────────────────────────────────────'

stylize "${BAR}"                                              "bold,${EMPHASIS}"
stylize "  ████████▄    ▄████████   ▄█▀▀▀█▀▀█      ▄▄▄▄▄▄▄ "  "bold,${EMPHASIS}"
stylize "  ██   ▀██   ██▀     ▀██   ██  █  ██     ██     ██" "bold,${EMPHASIS}"
stylize "  ██    ██   ██       ██   ██▄▄█▄▄██     ██▄▄▄▄▄██" "bold,${EMPHASIS}"
stylize "  ██    ██   ██       ██   ██  █  ██     ██     ██" "bold,${EMPHASIS}"
stylize "  ██   ▄██   ██▄     ▄██   ██  █  ██     ██     ██" "bold,${EMPHASIS}"
stylize "  ████████▀    ▀████████   ▀█▄▄█▄▄█▀     ██     ██" "bold,${EMPHASIS}"
stylize "${BAR}"                                              "bold,${EMPHASIS}"

colorize "  ${GREETING}, ${TARGET}!"           "bold"
printf   "  %s\n"                              "${DASH}"

# Pretty key/value rows
_row() { printf "  %-14s %s\n" "$1" "$2"; }
_row "Image"      "$(stylize "myapp:latest"     "cyan")"
_row "Version"    "$(stylize "${APP_VERSION}"   "green")"
_row "Shell"      "$(stylize "/bin/sh (busybox ash)" "yellow")"
_row "Working"    "$(stylize "${PWD}"           "magenta")"
_row "PID"        "$(stylize "$$"               "blue")"
_row "Uname"      "$(stylize "$(uname -a 2>/dev/null || echo unknown)" "dim")"

printf "  %s\n" "${DASH}"
colorize "  Container exited cleanly. Built with Docksmith." "dim"
stylize  "${BAR}"                                            "bold,${EMPHASIS}"
# bump
# bump
# bump
