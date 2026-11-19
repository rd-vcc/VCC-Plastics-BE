"""Resolve employee codes stored in audit / owner columns to "full name + code".

Codes may have lost their leading zeros (842 instead of 00842) when they were stored in
numeric columns, so matching is done on the code without leading zeros.
"""


def _key(value):
    return str(value).strip().lstrip("0") if value not in (None, "") else None


def employee_label(cur, code):
    """"Full name - code" for one employee code (the code alone when it is not in the directory)."""
    if not code:
        return code
    row = attach_employee_names(cur, [{"code": code}], ["code"])[0]
    return f"{row['code_name']} - {row['code_code']}" if row["code_name"] else code


def active_employees(cur):
    """Active employees for pickers: [{employee_code, full_name, position}] from the directory plus local accounts."""
    try:
        cur.execute("""SELECT employee_code, full_name, position FROM nsh.employees2026_base
                       WHERE employment_status='active' AND employee_code IS NOT NULL""")
        rows = cur.fetchall()
    except Exception:  # no employee directory on this server
        rows = []
    try:  # plus local VCC Plastics accounts that are not in the directory
        known = {_key(r["employee_code"]) for r in rows}
        cur.execute("SELECT employee_code, full_name, position FROM mes_users WHERE is_active = 1 AND full_name IS NOT NULL")
        rows += [r for r in cur.fetchall() if _key(r["employee_code"]) not in known]
    except Exception:
        pass
    # the directory has stray spaces and a few broken characters: tidy names before showing them
    clean = lambda v: str(v).encode("utf-8", "replace").decode("utf-8").replace("?", "").strip() if v else v
    rows = [{**r, "full_name": clean(r["full_name"])} for r in rows]
    return sorted(rows, key=lambda r: (r["full_name"] or "", r["employee_code"]))


def attach_employee_names(cur, rows, fields):
    """For every field in `fields` (e.g. ["actor"] or ["created_by", "approved_by"]) add
    `<field>_name` (full name or None) and `<field>_code` (full employee code, or the raw value)."""
    rows = rows if isinstance(rows, list) else [rows]
    codes = sorted({_key(row.get(field)) for row in rows if row for field in fields} - {None, ""})
    employees = {}
    if codes:
        try:
            cur.execute(
                f"""SELECT employee_code, full_name FROM nsh.employees2026_base
                    WHERE TRIM(LEADING '0' FROM employee_code) COLLATE utf8mb4_unicode_ci IN ({", ".join(["%s"] * len(codes))})""",
                tuple(codes),
            )
            employees = {_key(r["employee_code"]): r for r in cur.fetchall()}
        except Exception:  # employee directory not reachable: local accounts below
            employees = {}
        missing = [c for c in codes if c not in employees]
        if missing:  # local VCC Plastics accounts (mes_users.full_name) fill the gaps
            try:
                cur.execute(
                    f"""SELECT employee_code, full_name FROM mes_users
                        WHERE full_name IS NOT NULL AND TRIM(LEADING '0' FROM employee_code) IN ({", ".join(["%s"] * len(missing))})""",
                    tuple(missing),
                )
                employees.update({_key(r["employee_code"]): r for r in cur.fetchall()})
            except Exception:
                pass
    for row in rows:
        if not row:
            continue
        for field in fields:
            employee = employees.get(_key(row.get(field)))
            row[f"{field}_name"] = str(employee["full_name"]).strip() if employee and employee["full_name"] else None
            row[f"{field}_code"] = employee["employee_code"] if employee else row.get(field)
    return rows
