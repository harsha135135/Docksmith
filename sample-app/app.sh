#!/bin/sh
# Sample Docksmith application — pure POSIX shell, runs on Alpine base image.
#
# ENV vars (overridable with -e at runtime):
#   GREETING    — greeting word  (default: Hello)
#   TARGET      — who to greet   (default: Docksmith)
#   APP_VERSION — set via Docksmithfile ENV
#   EMPHASIS    — greeting color (default: green)

GREETING="${GREETING:-Hello}"
TARGET="${TARGET:-Docksmith}"
APP_VERSION="${APP_VERSION:-unknown}"
EMPHASIS="${EMPHASIS:-green}"

# Source the vendor library
. /app/vendor/colorize.sh

BANNER="========================================"
echo "$BANNER"
colorize "  ${GREETING}, ${TARGET}!" "${EMPHASIS}"
echo "  App version : ${APP_VERSION}"
echo "  Shell       : /bin/sh (busybox ash)"
echo "  PID         : $$"
echo "  Working dir : ${PWD}"
echo "$BANNER"
