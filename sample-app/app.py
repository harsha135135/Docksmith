#!/usr/bin/env python3
"""
Sample Docksmith application.

Demonstrates:
  - Reading ENV variables (GREETING, TARGET overridable via -e at runtime)
  - Using bundled vendor dependency (vendor/colorize.py)
  - Producing visible output

Usage inside container:
  docksmith run myapp:latest
  docksmith run -e GREETING=Howdy -e TARGET=World myapp:latest
"""

import os
import sys

# Add vendor directory to path
sys.path.insert(0, "/app/vendor")

try:
    from colorize import colorize
except ImportError:
    def colorize(text, color=None):
        return text

GREETING = os.environ.get("GREETING", "Hello")
TARGET = os.environ.get("TARGET", "Docksmith")
VERSION = os.environ.get("APP_VERSION", "unknown")
EMPHASIS = os.environ.get("EMPHASIS", "green")

banner = "=" * 40
print(banner)
print(colorize(f"  {GREETING}, {TARGET}!", color=EMPHASIS))
print(f"  App version : {VERSION}")
print(f"  Python      : {sys.version.split()[0]}")
print(f"  Working dir : {os.getcwd()}")
print(banner)
