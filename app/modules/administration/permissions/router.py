from fastapi import APIRouter

from app.modules.administration.permissions import service

router = APIRouter(prefix="/api/permissions", tags=["Permissions"])


@router.get("")
def list_permissions():
    """Danh sach permission co dinh cua he thong. Khong cho phep tao/sua/xoa qua API."""
    return service.list_permissions()


@router.get("/grouped")
def list_permissions_grouped():
    """Tra permission theo module de FE Role & Permission render checkbox theo nhom."""
    return service.list_permissions_grouped()
