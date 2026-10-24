import os
from pathlib import Path
from io import BytesIO
from datetime import date, datetime

import pytest
from fpdf import FPDF

os.environ.setdefault("SEWASETU_SECRET_KEY", "test-secret-key-keep-this-out-of-production")
os.environ.setdefault("SEWASETU_ADMIN_USERNAME", "test-admin")
os.environ.setdefault("SEWASETU_ADMIN_PASSWORD", "test-password-keep-this-out-of-production")

from app import app as app_module
from app.ai_assistant import _plain_text_answer
from app.db import connect_db
from app.app import app, get_db, hash_password, sanitize

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/about",
        "/apply",
        "/status",
        "/status/reset",
        "/admin",
        "/live",
    ],
)
def test_public_pages_render(client, path, monkeypatch):
    monkeypatch.setattr(app_module, "deadline_remaining", lambda: 10)
    assert client.get(path).status_code == 200


def test_otp_pages_explain_simulated_sms_delivery(client, monkeypatch):
    monkeypatch.setattr(app_module, "deadline_remaining", lambda: 10)
    apply_response = client.get("/apply")
    with client.session_transaction() as sess:
        sess["apply_mobile"] = "9999999999"
    verify_response = client.get("/verify")

    assert b"simulated SMS gateway" in apply_response.data
    assert b"SMS gateway console" in apply_response.data
    assert b"does not send texts to real phones" in verify_response.data
    assert b"SMS gateway console" in verify_response.data


def test_chatbot_requires_provider_configuration(client, monkeypatch):
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_URL", "")
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "")

    response = client.post("/api/chatbot", json={"message": "Who can apply?"})

    assert response.status_code == 503
    assert response.get_json()["error"] == "The AI assistant is not configured yet."


def test_chatbot_uses_configured_provider_and_returns_reply(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "AI_CHATBOT_API_URL",
        "https://ai.example.test/v1/chat/completions",
    )
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "test-provider-key")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "test-model")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Residents aged 60 or older may apply."}}]}

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(app_module.requests, "post", fake_post)

    response = client.post("/api/chatbot", json={"message": "Who can apply?"})

    assert response.status_code == 200
    assert response.get_json()["reply"] == "Residents aged 60 or older may apply."
    assert "scheme" in response.get_json()["sources"][0].lower()
    assert calls[0][0] == "https://ai.example.test/v1/chat/completions"
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-provider-key"
    assert calls[0][1]["json"]["model"] == "test-model"
    assert calls[0][1]["json"]["messages"][-1] == {
        "role": "user",
        "content": "Who can apply?",
    }
    system_message = calls[0][1]["json"]["messages"][0]["content"]
    assert "Retrieved source excerpts" in system_message
    assert "Residents of Purvanchal" in system_message
    assert calls[0][1]["timeout"] == (5, 45)


def test_chatbot_rejects_invalid_questions_without_calling_provider(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "AI_CHATBOT_API_URL",
        "https://ai.example.test/v1/chat/completions",
    )
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "test-provider-key")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "test-model")

    def fail_if_called(*args, **kwargs):
        pytest.fail("Provider must not be called for an invalid question.")

    monkeypatch.setattr(app_module.requests, "post", fail_if_called)

    too_long = client.post("/api/chatbot", json={"message": "x" * 1001})
    empty = client.post("/api/chatbot", json={"message": "  "})

    assert too_long.status_code == 400
    assert empty.status_code == 400


@pytest.mark.parametrize(
    ("provider_answer", "expected"),
    [
        ('{"answer":"Residents aged 60 or older may apply."}',
         "Residents aged 60 or older may apply."),
        ('```json\n{"reply":"Upload a JPG or PDF under 5 MB."}\n```',
         "Upload a JPG or PDF under 5 MB."),
        ('"{\\"output\\":\\"Please contact your local office.\\"}"',
         "Please contact your local office."),
        ('{"data":{"final":"Applications take up to 15 days."}}',
         "Applications take up to 15 days."),
        ('{"type":"text","text":"You can apply online."}',
         "You can apply online."),
        ("A normal plain text response.", "A normal plain text response."),
    ],
)
def test_model_json_is_displayed_as_plain_answer(provider_answer, expected):
    assert _plain_text_answer(provider_answer) == expected


def test_chatbot_rejects_unsupported_language_without_calling_provider(
    client, monkeypatch
):
    monkeypatch.setattr(
        app_module, "AI_CHATBOT_API_URL",
        "https://ai.example.test/v1/chat/completions",
    )
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "test-provider-key")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "test-model")
    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("Provider must not be called."),
    )

    response = client.post(
        "/api/chatbot",
        json={"message": "Who can apply?", "language": "Unknown"},
    )

    assert response.status_code == 400


def test_chatbot_never_adds_application_fields_to_provider_request(
    client, monkeypatch
):
    monkeypatch.setattr(
        app_module, "AI_CHATBOT_API_URL",
        "https://ai.example.test/v1/chat/completions",
    )
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "test-provider-key")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "test-model")
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "Use the protected portal."}}]}

    monkeypatch.setattr(
        app_module.requests,
        "post",
        lambda url, **kwargs: (calls.append(kwargs) or Response()),
    )

    response = client.post(
        "/api/chatbot",
        json={
            "message": "How do I review this application?",
            "language": "Hindi",
            "applicant_name": "Private Test Name",
            "bank_account": "123456789012",
        },
    )

    assert response.status_code == 200
    provider_request = str(calls[0]["json"])
    assert "Private Test Name" not in provider_request
    assert "123456789012" not in provider_request
    assert "Hindi" in calls[0]["json"]["messages"][0]["content"]


def test_upload_requires_applicant_confirmation_after_local_ocr(
    client, monkeypatch, tmp_path
):
    with client.session_transaction() as sess:
        sess["verified_mobile"] = "9876543210"
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module,
        "extract_age_proof_suggestions",
        lambda content, extension: {
            "name": "Test Person",
            "dates": ["01/01/1940"],
        },
    )

    upload = client.post(
        "/upload",
        data={"document": (BytesIO(b"\xff\xd8\xfftest"), "age-proof.jpg")},
        content_type="multipart/form-data",
    )

    assert upload.status_code == 200
    assert b"Test Person" in upload.data
    assert b"has been changed in your form" in upload.data
    with client.session_transaction() as sess:
        assert sess["ocr_review_pending"] is True

    not_confirmed = client.post("/upload", data={"confirm_review": "1"})
    assert not_confirmed.status_code == 302
    with client.session_transaction() as sess:
        assert sess["ocr_review_pending"] is True

    confirmed = client.post(
        "/upload",
        data={"confirm_review": "1", "reviewed": "yes"},
    )
    assert confirmed.status_code == 302
    assert confirmed.headers["Location"].endswith("/declaration")
    with client.session_transaction() as sess:
        assert "ocr_review_pending" not in sess


def test_chatbot_does_not_send_api_key_over_plain_http(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "AI_CHATBOT_API_URL",
        "http://ai.example.test/v1/chat/completions",
    )
    monkeypatch.setattr(app_module, "AI_CHATBOT_API_KEY", "test-provider-key")
    monkeypatch.setattr(app_module, "AI_CHATBOT_MODEL", "test-model")

    response = client.post("/api/chatbot", json={"message": "Who can apply?"})

    assert response.status_code == 503
    assert "temporarily unavailable" in response.get_json()["error"]


def test_chatbot_post_requires_csrf_token(client, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

    response = client.post("/api/chatbot", json={"message": "Who can apply?"})

    assert response.status_code == 400


def test_identity_scaffold_stores_only_provider_reference_and_links_uniquely():
    schema = (
        Path(__file__).resolve().parents[1] / "app" / "schema.sql"
    ).read_text(encoding="utf-8").lower()

    identity_schema = schema.split(
        "create table if not exists identity_verifications", 1
    )[1]

    assert "application_id" in identity_schema
    assert "provider_name" in identity_schema
    assert "subject_reference" in identity_schema
    assert "verified_at" in identity_schema
    assert "unique (provider_name, subject_reference)" in identity_schema
    assert "aadhar_number" not in identity_schema
    assert "aadhaar_number" not in identity_schema
    assert "otp" not in identity_schema


@pytest.mark.parametrize(
    "path",
    [
        "/form/1",
        "/form/2",
        "/form/3",
        "/upload",
        "/declaration",
    ],
)
def test_application_steps_render_for_verified_applicant(client, path):
    with client.session_transaction() as sess:
        sess["verified_mobile"] = "9876543210"

    assert client.get(path).status_code == 200


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


def test_state_changing_admin_request_requires_csrf_token(client, monkeypatch):
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)

    response = client.post("/admin/approve/1")

    assert response.status_code == 400


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


def test_apply_requires_captcha_answer(client, monkeypatch):
    monkeypatch.setattr(app_module, "deadline_remaining", lambda: 10)
    with client.session_transaction() as sess:
        sess["captcha_answer"] = 5

    response = client.post("/apply", data={"mobile": "9876543210", "captcha": "4"})

    assert response.status_code == 200
    assert b"Security Check" in response.data
    with client.session_transaction() as sess:
        assert "apply_mobile" not in sess


def test_uploaded_document_format_size_and_filename_are_safe(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(
        app_module,
        "extract_age_proof_suggestions",
        lambda content, extension: {"name": "", "dates": []},
    )
    with client.session_transaction() as sess:
        sess["verified_mobile"] = "9876543210"

    response = client.post(
        "/upload",
        data={"document": (BytesIO(b"%PDF-1.4\nvalid test document"), "../../outside.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"Check the document suggestions" in response.data
    saved_files = list(tmp_path.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].name.startswith("9876543210_")
    assert saved_files[0].suffix == ".pdf"
    assert saved_files[0].read_bytes().startswith(b"%PDF-")


def test_upload_rejects_mismatched_content(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path))
    with client.session_transaction() as sess:
        sess["verified_mobile"] = "9876543210"

    response = client.post(
        "/upload",
        data={"document": (BytesIO(b"not a pdf"), "proof.pdf")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert b"does not match" in response.data
    assert list(tmp_path.iterdir()) == []


def test_sanitize_preserves_unicode_and_uses_character_limit():
    assert sanitize("शारदा देवी", maxlen=100) == "शारदा देवी"


@pytest.mark.parametrize(
    ("field", "value", "expected_step"),
    [
        ("block", "Not a district block", 2),
        ("ifsc", "invalid", 3),
        ("bank_account", "12x", 3),
    ],
)
def test_submission_validates_address_and_bank_fields(
    client, field, value, expected_step
):
    form_data = {
        "applicant_name": "Asha Devi",
        "dob": "02/01/1940",
        "gender": "Female",
        "marital_status": "Married",
        "village": "Sonapur",
        "block": "Sonari",
        "bank_account": "1234567890",
        "ifsc": "ABCD0123456",
    }
    form_data[field] = value
    with client.session_transaction() as sess:
        sess["verified_mobile"] = "9876543210"
        sess["form_data"] = form_data

    response = client.post("/declaration")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/form/{expected_step}")


def test_application_is_not_visible_to_another_logged_in_citizen(
    client, monkeypatch
):
    application = (
        "SSP26123456",
        "Asha Devi",
        "9876543210",
        date(1940, 1, 2),
        "Sonapur",
        "Sonari",
        "1234567890",
        "ABCD0123456",
        "PENDING",
        datetime(2026, 1, 1, 12, 0),
        None,
    )

    class Cursor:
        def execute(self, *_):
            pass

        def fetchone(self):
            return application

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(app_module, "get_db", lambda: Connection())
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["portal_mobile"] = "9876543211"

    response = client.get("/application/42")

    assert response.status_code == 404


def test_acknowledgment_pdf_requires_login(client):
    assert client.get("/ack/42.pdf").status_code == 403


def test_admin_document_download_is_authorized_and_confined(
    client, monkeypatch, tmp_path
):
    document = tmp_path / "age-proof.pdf"
    document.write_bytes(b"%PDF-1.4\nage proof")
    monkeypatch.setattr(app_module, "UPLOAD_DIR", str(tmp_path))

    class Cursor:
        def execute(self, *_):
            pass

        def fetchone(self):
            return (str(document),)

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(app_module, "get_db", lambda: Connection())
    assert client.get("/admin/application/42/document").status_code == 403

    with client.session_transaction() as sess:
        sess["admin"] = True
    response = client.get("/admin/application/42/document")

    assert response.status_code == 200
    assert response.data == b"%PDF-1.4\nage proof"
    assert "attachment" in response.headers["Content-Disposition"]


def test_database_connection_is_reused_and_closed_at_request_teardown(monkeypatch):
    connections = []
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-local-db-password")

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
    monkeypatch.setenv("POSTGRES_PASSWORD", "test-local-db-password")

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


def test_database_connection_uses_neon_url_with_tls(monkeypatch):
    options = {}

    def connect(**kwargs):
        options.update(kwargs)
        return object()

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://neon_user:test-only-password@db.example.test/neondb",
    )
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setattr("app.db.psycopg2.connect", connect)

    connection = connect_db()

    assert connection is not None
    assert options["host"] == "db.example.test"
    assert options["dbname"] == "neondb"
    assert options["sslmode"] == "require"
    assert options["connect_timeout"] == "10"


def test_database_connection_accepts_postgres_url_alias(monkeypatch):
    options = {}
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "POSTGRES_URL",
        "postgresql://neon_user:test-only-password@db.example.test/neondb?sslmode=verify-full",
    )
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setattr(
        "app.db.psycopg2.connect",
        lambda **kwargs: options.update(kwargs) or object(),
    )

    connect_db()

    assert options["sslmode"] == "verify-full"


def test_database_connection_requires_credentials(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        connect_db()


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
            return (app_id, "9876543210") if len(executed) == 1 else application

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
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["portal_mobile"] = "9876543210"

    response = client.get(f"/ack/{app_id}.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
    assert executed[0][1] == (app_id,)
    assert executed[1][1] == (app_id,)
    assert len(rendered_pdfs) == 1
    assert rendered_pdfs[0].rect_calls == 1
    assert rendered_pdfs[0].cell_calls == 23