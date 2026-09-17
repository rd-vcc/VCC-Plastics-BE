"""Thin orchestration layer over app.modules.administration.permissions.repository."""

from app.modules.administration.permissions import repository


def list_permissions():
    return repository.list_permissions()


def list_permissions_grouped():
    return repository.list_permissions_grouped()
