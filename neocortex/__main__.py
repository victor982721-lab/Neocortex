"""Support ``python -m neocortex`` through the installed entry point."""

from .cli import entrypoint

raise SystemExit(entrypoint())
