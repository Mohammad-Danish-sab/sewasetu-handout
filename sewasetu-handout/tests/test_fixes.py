from datetime import date, datetime

import pytest
from fpdf import FPDF
from app import app as app_module
from app.app import app, get_db, hash_password

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client

@pytest.mark.parametrize("endpoint", ["/admin/approve/1", "/admin/reject/1"])
@pytest.mark.parametrize("admin_value", [False, None, 1, "true"])
def test_admin_authorization_enforced(client, endpoint, admin_value):
    """Only an actual boolean admin session may change application status."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["portal_mobile"] = "9876543210"
        if admin_value is not None:
            sess["admin"] = admin_value

    response = client.post(endpoint)
    assert response.status_code == 403

def test_password_reset_uses_parameterized_mobile_lookup(client, monkeypatch):
    """Treat an injection-shaped mobile value as data in the reset lookup."""
    injection = "9876543210' OR '1'='1"
    executed = []

    class Cursor:
        def execute(self, query, params=None):
            executed.append((query, params))

        def fetchone(self):
            return None

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr("app.app.get_db", lambda: Connection())
    response = client.post("/status/reset", data={
        "mobile": injection
    })

    assert response.status_code == 200
    assert executed == [
        ("SELECT mobile FROM portal_users WHERE mobile = %s", (injection,))
    ]

def test_password_reset_parameterizes_account_lookup_and_update(client, monkeypatch):
    mobile = "9876543210"
    executed = []
    results = [(mobile,), (date(1940, 1, 2),)]
    connection_state = {"committed": False}

    class Cursor:
        def execute(self, query, params=None):
            executed.append((query, params))

        def fetchone(self):
            return results.pop(0)

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            connection_state["committed"] = True

        def close(self):
            pass

    monkeypatch.setattr("app.app.get_db", lambda: Connection())
    monkeypatch.setattr("app.app.send_sms", lambda *_: None)

    response = client.post("/status/reset", data={"mobile": mobile})

    assert response.status_code == 200
    assert executed == [
        ("SELECT mobile FROM portal_users WHERE mobile = %s", (mobile,)),
        ("SELECT dob FROM applications WHERE mobile = %s LIMIT 1", (mobile,)),
        (
            "UPDATE portal_users SET password_hash = %s WHERE mobile = %s",
            (hash_password("02011940"), mobile),
        ),
    ]
    assert connection_state["committed"]

def test_database_connection_is_reused_and_closed_at_request_teardown(monkeypatch):
    connections = []

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    def connect(**kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("app.app.psycopg2.connect", connect)
    with app.app_context():
        first = get_db()
        assert get_db() is first

    assert len(connections) == 1
    assert connections[0].closed

def test_database_connection_reopens_after_explicit_close(monkeypatch):
    connections = []

    class Connection:
        closed = False

        def close(self):
            self.closed = True

    def connect(**kwargs):
        connection = Connection()
        connections.append(connection)
        return connection

    monkeypatch.setattr("app.app.psycopg2.connect", connect)
    with app.app_context():
        first = get_db()
        first.close()
        second = get_db()
        assert second is not first

    assert len(connections) == 2
    assert all(connection.closed for connection in connections)

def test_acknowledgment_pdf_executes_with_bounded_rendering(
    client, monkeypatch, tmp_path
):
    """Generate an acknowledgment PDF using a fixed number of drawing operations."""
    app_id = 42
    application = (
        "SSP26123456",
        "Asha Devi",
        "9876543210",
        date(1940, 1, 2),
        "Sonapur",
        "Sonari",
        "1234567890",
        "ABCD0123456",
        datetime(2026, 1, 1, 12, 0),
        "PENDING",
    )
    executed = []

    class Cursor:
        def execute(self, query, params=None):
            executed.append((query, params))

        def fetchone(self):
            return (app_id,) if len(executed) == 1 else application

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    class CountingPDF(FPDF):
        def __init__(self):
            super().__init__()
            self.rect_calls = 0
            self.cell_calls = 0

        def rect(self, *args, **kwargs):
            self.rect_calls += 1
            return super().rect(*args, **kwargs)

        def cell(self, *args, **kwargs):
            self.cell_calls += 1
            return super().cell(*args, **kwargs)

    rendered_pdfs = []

    def make_pdf():
        pdf = CountingPDF()
        rendered_pdfs.append(pdf)
        return pdf

    monkeypatch.setattr(app_module, "get_db", lambda: Connection())
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(app_module, "FPDF", make_pdf)

    response = client.get(f"/ack/{app_id}.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert executed[0][1] == (app_id,)
    assert executed[1][1] == (app_id,)
    assert len(rendered_pdfs) == 1
    assert rendered_pdfs[0].rect_calls == 1
    assert rendered_pdfs[0].cell_calls == 23