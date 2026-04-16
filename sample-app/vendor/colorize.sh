#!/bin/sh
# colorize.sh — minimal ANSI color helper for shell scripts.
# Source this file, then call: colorize "text" [color]
# Bundled as a vendor dependency; no network needed.

colorize() {
    _text="$1"
    _color="${2:-green}"
    case "$_color" in
        red)     printf '\033[31m%s\033[0m\n' "$_text" ;;
        green)   printf '\033[32m%s\033[0m\n' "$_text" ;;
        yellow)  printf '\033[33m%s\033[0m\n' "$_text" ;;
        blue)    printf '\033[34m%s\033[0m\n' "$_text" ;;
        *)       printf '%s\n' "$_text" ;;
    esac
}
