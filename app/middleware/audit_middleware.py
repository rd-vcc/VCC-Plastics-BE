"""Audit middleware (9.3 Audit Log).

Records every data-changing API call (POST / PUT / PATCH / DELETE) and exports into audit_logs:
user, role, module, action, record, IP, browser, result, severity, request payload and - for updates,
deletes and workflow actions on a record - the record read just before and just after the call
(an internal GET of the same resource, marked X-Audit-Internal so it is not audited itself).
Auditing never blocks or fails the business request.
"""

import json
import time
from urllib.parse import unquote

import httpx
from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.modules.administration.audit import service as audit

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _resource_path(method, path, action):
    """URL of the record touched by the call, readable with GET (or None)."""
    parts = path.rstrip("/").split("/")
    if method in ("PUT", "PATCH", "DELETE"):
        return path
    if method == "POST" and action not in ("CREATE", "EXPORT", "FAVORITE", "UPLOAD", "RUN") and len(parts) > 3:
        return "/".join(parts[:-1])  # /x/{id}/{action} -> /x/{id}
    return None


async def _snapshot(app, url, headers):
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://audit.internal", timeout=5.0) as client:
            r = await client.get(url, headers={**headers, "X-Audit-Internal": "1"})
        if r.status_code == 200 and "application/json" in r.headers.get("content-type", "") and len(r.content) < 400_000:
            return r.json()
    except Exception:
        return None
    return None


async def audit_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    if request.headers.get("X-Audit-Internal") or not path.startswith("/api/") or path.startswith(audit.SKIP_PATHS):
        return await call_next(request)
    is_export = method == "GET" and ("/export" in path or "download" in path)
    if method not in WRITE_METHODS and not is_export:
        return await call_next(request)
    if not await run_in_threadpool(audit.enabled, "audit.enabled"):
        return await call_next(request)
    if is_export and not await run_in_threadpool(audit.enabled, "audit.log_exports"):
        return await call_next(request)

    started = time.perf_counter()
    raw = await request.body()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    request._receive = receive  # the endpoint reads the same body again

    body = None
    if raw and "json" in request.headers.get("content-type", ""):
        try:
            body = json.loads(raw)
        except Exception:
            body = None
    module, action, data_type, record = audit.classify(method, path)
    capture = await run_in_threadpool(audit.enabled, "audit.capture_changes")
    fwd = {k: v for k, v in request.headers.items() if k.lower() in ("authorization", "x-mes-user", "x-mes-user-name")}
    res_path = _resource_path(method, path, action) if capture else None
    before = await _snapshot(request.app, res_path, fwd) if res_path else None

    response = await call_next(request)

    data = None
    ctype = response.headers.get("content-type", "")
    if "application/json" in ctype:
        chunks = [c async for c in response.body_iterator]
        content = b"".join(chunks)
        try:
            data = json.loads(content) if content else None
        except Exception:
            data = None
        response = Response(content=content, status_code=response.status_code, headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
                            media_type=ctype)
    ok = response.status_code < 400
    after = None
    if ok and res_path and method != "DELETE":
        after = await _snapshot(request.app, res_path, fwd)
    if ok and not record:
        record = audit.record_from_response(data)
    user_code = unquote(request.headers.get("X-MES-User", "")) or None
    user_name = unquote(request.headers.get("X-MES-User-Name", "")) or None
    body_actor = audit.actor_from_body(body)
    who = user_code or body_actor
    name = user_name or body_actor or user_code
    message = None
    if not ok and isinstance(data, dict):
        d = data.get("detail")
        message = d if isinstance(d, str) else json.dumps(d, ensure_ascii=False, default=str)[:500] if d else None
    result = "SUCCESS" if ok else "FAILED"
    entry = {
        "user_code": who, "user_name": name, "role_name": await run_in_threadpool(audit.roles_of, who), "module": module, "action": action, "data_type": data_type,
        "record_id": record, "description": f"{method} {path}", "method": method, "path": path[:500],
        "ip_address": (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else None)),
        "user_agent": (request.headers.get("user-agent") or "")[:300], "session_id": (request.headers.get("X-MES-Session") or "")[:64] or None,
        "result": result, "status_code": response.status_code, "severity": audit.severity(module, action, result, response.status_code), "message": message and str(message)[:500],
        "before_json": audit.dumps(before), "after_json": audit.dumps(after if after is not None else (data if ok and method == "POST" and action == "CREATE" else None)),
        "request_json": audit.dumps(body) if body is not None and module != "FILES" else None, "duration_ms": int((time.perf_counter() - started) * 1000), "source": "API",
    }
    await run_in_threadpool(audit.write, entry)
    return response
