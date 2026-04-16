#!/bin/sh
# colorize.sh — minimal ANSI styling helpers for shell scripts.
# Source this file, then call: colorize "text" [color]
# Bundled as a vendor dependency; no network needed.

# Detect whether stdout is a TTY; auto-disable colors when piped.
if [ -t 1 ]; then
    _DS_TTY=1
else
    _DS_TTY=0
fi

_ds_code() {
    case "$1" in
        red)        printf '31' ;;
        green)      printf '32' ;;
        yellow)     printf '33' ;;
        blue)       printf '34' ;;
        magenta)    printf '35' ;;
        cyan)       printf '36' ;;
        white)      printf '37' ;;
        bold)       printf '1'  ;;
        dim)        printf '2'  ;;
        *)          printf '0'  ;;
    esac
}

# colorize "text" [color]
colorize() {
    _text="$1"
    _color="${2:-green}"
    if [ "${_DS_TTY}" = "1" ]; then
        _c="$(_ds_code "${_color}")"
        printf '\033[%sm%s\033[0m\n' "${_c}" "${_text}"
    else
        printf '%s\n' "${_text}"
    fi
}

# stylize "text" "color1,color2,..."  — combine multiple SGR codes (e.g. bold,cyan)
stylize() {
    _text="$1"
    _styles="${2:-bold}"
    if [ "${_DS_TTY}" != "1" ]; then
        printf '%s\n' "${_text}"
        return
    fi
    _seq=""
    _IFS_SAVE="$IFS"
    IFS=','
    for s in ${_styles}; do
        _c="$(_ds_code "${s}")"
        if [ -z "${_seq}" ]; then
            _seq="${_c}"
        else
            _seq="${_seq};${_c}"
        fi
    done
    IFS="${_IFS_SAVE}"
    printf '\033[%sm%s\033[0m\n' "${_seq}" "${_text}"
}
