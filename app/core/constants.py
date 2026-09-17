"""Shared constants used across modules.

``MODULE_CODES`` documents the permission ``module_code`` values that
``scripts/seed_permissions.py`` seeds into the ``permissions`` table (see the
``PERMISSIONS`` list there). The seed script keeps its own literal tuples
unchanged to avoid any risk to the seeding behavior; this list exists so new
code has a single, documented place to look up the valid module codes.
"""

MODULE_CODES = [
    "DASHBOARD",
    "PRODUCTION",
    "MACHINE",
    "MOLD",
    "MATERIAL",
    "QUALITY",
    "MAINTENANCE",
    "TRACEABILITY",
    "REPORTS",
    "ADMINISTRATION",
    "SYSTEM_CONFIGURATION",
]
