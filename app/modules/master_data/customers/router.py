"""Minimal Customer master (roadmap step 2.1).

Only what Production Orders need today: list, create, update. A full Customer /
Supplier master screen can grow from here.
"""

from fastapi import APIRouter, HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.customers.schemas import CustomerCreate, CustomerUpdate


router = APIRouter(prefix="/api/customers", tags=["Customer Master"])


def _run_write(action):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        if "Duplicate" in str(exc):
            raise HTTPException(status_code=409, detail="Customer code already exists.") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


@router.get("")
def list_customers(keyword: str | None = None, status: str | None = None):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        conditions, params = [], []
        if keyword:
            conditions.append("(customer_code LIKE %s OR customer_name LIKE %s OR short_name LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value, value, value]
        if status:
            conditions.append("status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(f"SELECT * FROM customers{where} ORDER BY customer_code", tuple(params))
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.post("", status_code=201)
def create_customer(payload: CustomerCreate):
    def action(cur):
        cur.execute(
            """INSERT INTO customers (customer_code, customer_name, short_name, country, contact_name,
               contact_email, contact_phone, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.customer_code, payload.customer_name, payload.short_name, payload.country, payload.contact_name,
             payload.contact_email, payload.contact_phone, payload.status, payload.created_by),
        )
        return {"message": "Customer created.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/{customer_id}")
def update_customer(customer_id: int, payload: CustomerUpdate):
    def action(cur):
        cur.execute(
            """UPDATE customers SET customer_code=%s, customer_name=%s, short_name=%s, country=%s, contact_name=%s,
               contact_email=%s, contact_phone=%s, status=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.customer_code, payload.customer_name, payload.short_name, payload.country, payload.contact_name,
             payload.contact_email, payload.contact_phone, payload.status, payload.updated_by, customer_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Customer not found or changed by another user. Reload and try again.")
        return {"message": "Customer updated.", "version": payload.version + 1}
    return _run_write(action)
