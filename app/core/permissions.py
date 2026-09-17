"""Permission-checking helpers placeholder.

Nothing in the current codebase enforces View/Edit permissions at the API
layer. Permissions are only computed and returned to the frontend as part of
the login response (``mes_access.permissions`` - see
``app.modules.auth.repository.get_mes_access``); no router today checks them
before executing a request.

This module is reserved for a future dependency-based permission check (for
example a ``require_permission("module.action")`` FastAPI dependency) once
enforcement is actually designed and agreed on. Do NOT wire enforcement into
any existing router from this module without an explicit product decision -
doing so would change behavior for the live frontend, which does not send an
Authorization header today.
"""
