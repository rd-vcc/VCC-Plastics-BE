"""Shared normalizers for NEW schemas.

Mirrors the repeated normalization patterns already present in the ported
schemas (for example ``_upper()`` in
``app.modules.master_data.machines.schemas`` and
``app.modules.master_data.products.schemas``, or the ``.strip()`` calls
throughout ``app.modules.master_data.factory_structure.schemas``). Existing
schemas keep their own local copies of these helpers unchanged, to avoid any
risk of altering validation behavior for the live frontend.
"""


def upper(value: str) -> str:
    return value.strip().upper()


def clean_required(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Value cannot be blank.")
    return value
