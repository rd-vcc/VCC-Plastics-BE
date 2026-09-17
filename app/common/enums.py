"""Shared status enums for NEW code.

Existing Pydantic schemas keep their own local ``Literal``/``set``
definitions unchanged (e.g. ``ASSET_STATUSES`` / ``OPERATIONAL_STATUSES`` in
``app.modules.master_data.machines.schemas``, or ``ActiveStatus`` /
``MoldStatus`` in ``app.modules.master_data.molds.schemas``) to avoid any
risk of altering validation behavior for the live frontend. These enums are
available for new schemas that want a shared, importable definition.
"""

from enum import Enum


class ActiveStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AssetStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    RETIRED = "RETIRED"
    SCRAPPED = "SCRAPPED"


class OperationalStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"
    OFFLINE = "OFFLINE"
