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

Tao database bang `database/schema.sql`, sau do seed permission co dinh:

```powershell
python -m database.seed_permissions
```

Chay BE:

```powershell
uvicorn main:app --reload --host 0.0.0.0 --port 5000
```

Swagger:

`http://127.0.0.1:5000/docs`

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

## Login flow

FE -> VCC Plastics `/api/auth/login` -> VCC Group `/api/login` -> kiem tra `mes_users` -> lay Role -> lay Permission -> tra ve FE.
