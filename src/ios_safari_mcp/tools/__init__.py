"""Importing this package registers every tool (the order is the order of tools/list)."""
from . import guest, input, inspector, keyboard, screen, vm  # noqa: F401  (imported for their @tool registrations)
