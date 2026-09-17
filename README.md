# VCC Plastics Backend - Foundation Authorization

FastAPI + mysql.connector backend cho giai doan Foundation / Login / Role & Permission.

## Nguyen tac

- VCC Group xac thuc username/password va cung cap thong tin nhan vien.
- VCC Plastics quan ly ai duoc vao MES bang `mes_users`.
- VCC Plastics tu quan ly Role va Permission.
- Permission la danh muc **co dinh do developer seed**, khong co API tao/sua/xoa permission cho Admin.
- Admin chi tao/sua Role, gan Permission vao Role, them MES User va gan Role cho User.

## 5 bang database

- `mes_users`
- `roles`
- `permissions`
- `user_roles`
- `role_permissions`

## Cai dat

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Database da duoc tao san tren MySQL dev (schema `vcc_plastics`). Neu can tao
lai tu dau, xem baseline schema trong `migrations/versions/0001_baseline.py`
(va `migrations/README.md` truoc khi dung Alembic voi database dang chay).
Sau do seed permission co dinh:

```powershell
python -m scripts.seed_permissions
```

Chay BE:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 5005
```

Swagger:

`http://127.0.0.1:5005/docs`

## Cau truc thu muc

Project theo layout modular-monolith:

- `main.py` - entrypoint, composition root (tao app, CORS, middleware, router)
- `app/core` - config, database, logging, exceptions, cac placeholder
  security/permissions cho tuong lai
- `app/common` - dependencies/pagination/validators/enums dung chung
- `app/middleware` - request logging + error handler (that su chay), cong
  voi auth/audit middleware placeholder (chua bat, chua co logic)
- `app/modules` - moi module nghiep vu co that (auth, administration.users,
  administration.roles, administration.permissions,
  administration.system_settings, organization.employees,
  master_data.factory_structure, master_data.products,
  master_data.machines, master_data.molds) co router/schemas/service rieng
- `app/integrations/vcc_group` - client HTTP goi API VCC Group ben ngoai
- `app/models` - SQLAlchemy declarative base + mixin cho model tuong lai
- `app/api` - `router.py` gom cac router that lai, `health.py` cho `/` va `/health`
- `migrations` - Alembic (xem `migrations/README.md` truoc khi chay)
- `tests` - pytest (`tests/api` co test cho health va auth)
- `scripts` - `seed_permissions.py` (that), `seed_admin.py` /
  `migrate_legacy_data.py` (placeholder, chua co spec)
- `storage`, `logs` - thu muc rong cho upload/export/log trong tuong lai

Cac module business khac (production, quality, maintenance,
material_management, iot, traceability, dashboard, notifications, va cac
submodule master_data/organization/administration khac) **chua duoc scaffold**
vi chua co code that - se duoc them khi co yeu cau cu the.

## API hien tai

### Authentication
- `POST /api/auth/login`

### MES Users
- `GET /api/users`
- `GET /api/users/{user_id}`
- `POST /api/users`
- `PUT /api/users/{user_id}`
- `GET /api/users/{user_id}/roles`
- `PUT /api/users/{user_id}/roles`
- `DELETE /api/users/{user_id}/roles/{role_id}`

### User Management (VCC Group -> VCC Plastics)
- `GET /api/user-management/employees` - lấy nhân viên VCC Group và ghép vai trò VCC Plastics
  - Query: `keyword`, `org_id`, `employment_status=active|inactive|all`, `assigned_only`
- `PUT /api/user-management/employees/{employee_code}/roles` - thêm/cập nhật MES user và thay toàn bộ vai trò
- `PATCH /api/user-management/users/{user_id}/status` - bật/tắt quyền truy cập VCC Plastics

Nguồn hồ sơ nhân viên là API VCC Group `GET /employees/list`. VCC Plastics chỉ
lưu `external_user_id`, `employee_code`, trạng thái truy cập và quan hệ vai trò.

### Roles
- `GET /api/roles`
- `GET /api/roles/{role_id}`
- `POST /api/roles`
- `PUT /api/roles/{role_id}`
- `DELETE /api/roles/{role_id}` (soft delete -> Inactive)

### Permissions
- `GET /api/permissions`
- `GET /api/permissions/grouped`

Khong co POST/PUT/DELETE Permission.

### Role Permissions
- `GET /api/roles/{role_id}/permissions`
- `PUT /api/roles/{role_id}/permissions`
- `DELETE /api/roles/{role_id}/permissions/{permission_id}`

### Product Master

Product Family tree:

- `GET /api/product-master/families`
- `GET /api/product-master/families/{family_id}`
- `POST /api/product-master/families`
- `PUT /api/product-master/families/{family_id}`
- `POST /api/product-master/families/{family_id}/move`
- `PATCH /api/product-master/families/{family_id}/status`
- `DELETE /api/product-master/families/{family_id}`

Dynamic product fields:

- `GET /api/product-master/fields`
- `GET /api/product-master/fields/{field_id}`
- `POST /api/product-master/fields`
- `PUT /api/product-master/fields/{field_id}`
- `DELETE /api/product-master/fields/{field_id}`

Products and revisions:

- `GET /api/product-master/products/kpis`
- `GET /api/product-master/products`
- `GET /api/product-master/products/{product_id}`
- `POST /api/product-master/products`
- `PUT /api/product-master/products/{product_id}`
- `PATCH /api/product-master/products/{product_id}/status`
- `DELETE /api/product-master/products/{product_id}` (soft delete)
- `GET /api/product-master/products/{product_id}/versions`
- `POST /api/product-master/products/{product_id}/versions`

Schema cho cac bang nay nam trong `migrations/versions/0001_baseline.py`
(da ap dung san tren DB dev).

## Login flow

FE -> VCC Plastics `/api/auth/login` -> VCC Group `/api/login` -> kiem tra `mes_users` -> lay Role -> lay Permission -> tra ve FE.
