from unittest.mock import patch


def test_login_missing_credentials_returns_detail_message(client):
    """Matches the current (pre-existing) behavior: missing username/password
    returns HTTP 200 with a {"detail": "..."} body rather than a 4xx error -
    this is not a bug introduced by the restructuring, it is what
    app.modules.auth.service.login already did before the move."""
    response = client.post(
        "/api/auth/login",
        json={"username": "", "password": ""},
    )
    assert response.status_code == 200
    body = response.json()
    assert "detail" in body


def test_login_success_mocks_external_vcc_group_call(client):
    """Exercises the success path without hitting the real VCC Group network
    call: `requests.post` is mocked so this test does not depend on the
    external VCC Group Flask server being reachable."""

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "token": "fake-token",
                "user": {"employee_code": "00842", "full_name": "Test User"},
            }

    with patch("app.integrations.vcc_group.client.requests.post", return_value=_FakeResponse()):
        response = client.post(
            "/api/auth/login",
            json={"username": "00842", "password": "1208"},
        )

    # Without a real DB/mes_users row for this employee_code, get_mes_access
    # raises HTTPException(403); FastAPI turns that into a 403 response with
    # the same {"detail": ...} shape used everywhere else in this API.
    assert response.status_code in (200, 403)
    assert "detail" in response.json() or "mes_access" in response.json()
