import os
import pytest
from app.app import app, get_db, hash_password

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        yield client

def test_admin_authorization_enforced(client):
    """Test that a regular logged-in citizen cannot access admin approval routes."""
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["admin"] = False  # Regular citizen session, not admin
        sess["portal_mobile"] = "9876543210"

    # Attempt to hit the admin approve endpoint
    response = client.post("/admin/approve/1", follow_redirects=True)
    
    # Must be forbidden (403)
    assert response.status_code == 403

def test_sql_injection_password_reset(client):
    """Test that SQL injection payloads in password reset do not cause syntax errors or bypass checks."""
    response = client.post("/status/reset", data={
        "mobile": "9876543210' OR '1'='1"
    }, follow_redirects=True)
    
    # Should handle gracefully without a 500 Internal Server Error
    assert response.status_code == 200
    assert b"reset" in response.data.lower() or b"exists" in response.data.lower()

def test_acknowledgment_pdf_performance(client):
    """Test that acknowledgment PDF generation executes cleanly."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id FROM applications LIMIT 1")
    row = cur.fetchone()
    if row:
        app_id = row[0]
        response = client.get(f"/ack/{app_id}.pdf")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
    cur.close()
    conn.close()