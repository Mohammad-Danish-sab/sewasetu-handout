# -*- coding: utf-8 -*-
"""
Sewa Setu - Old Age Pension Portal
Government of Purvanchal, Department of Social Welfare

Developed by: Netlink Infosolutions Pvt Ltd (2023)
Maintenance contract ended 31/01/2026.
"""

import os
import io
import csv
import random
import hashlib
import re
import secrets
import configparser
import uuid
from datetime import datetime, date, timedelta
from urllib.parse import urlsplit

import requests
import psycopg2
if __package__:
    from .ai_assistant import answer_question, LANGUAGES
    from .ai_workflows import (
        applicant_document_hints,
        application_status_guidance,
        operations_insights,
        staff_review_assistance,
    )
    from .db import connect_db
    from .document_ocr import extract_age_proof_suggestions, OCRUnavailableError
else:
    from ai_assistant import answer_question, LANGUAGES
    from ai_workflows import (
        applicant_document_hints,
        application_status_guidance,
        operations_insights,
        staff_review_assistance,
    )
    from db import connect_db
    from document_ocr import extract_age_proof_suggestions, OCRUnavailableError
from flask import (Flask, g, has_app_context, request, session, redirect,
                   url_for, render_template, flash, send_file, abort, jsonify)
from flask_wtf.csrf import CSRFProtect
from fpdf import FPDF

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

config = configparser.ConfigParser()
config.read(os.path.join(BASE_DIR, "config", "app.ini"))

app = Flask(__name__)
secret_key = os.environ.get("SEWASETU_SECRET_KEY")
if not secret_key:
    raise RuntimeError("SEWASETU_SECRET_KEY must be set before starting the portal.")
if len(secret_key) < 32:
    raise RuntimeError("SEWASETU_SECRET_KEY must contain at least 32 characters.")
app.secret_key = secret_key
app.config["PERMANENT_SESSION_LIFETIME"] = 300
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
CSRFProtect(app)

ADMIN_USERNAME = os.environ.get("SEWASETU_ADMIN_USERNAME")
ADMIN_PASSWORD = os.environ.get("SEWASETU_ADMIN_PASSWORD")
if not ADMIN_USERNAME or not ADMIN_PASSWORD:
    raise RuntimeError("Set SEWASETU_ADMIN_USERNAME and SEWASETU_ADMIN_PASSWORD.")
if len(ADMIN_PASSWORD) < 12:
    raise RuntimeError("SEWASETU_ADMIN_PASSWORD must contain at least 12 characters.")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
PDF_UNICODE_FONT = os.environ.get(
    "SEWASETU_UNICODE_FONT",
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
)

SMS_GATEWAY_URL = config.get("app", "sms_gateway_url")
OTP_VALIDITY_SECONDS = config.getint("app", "otp_validity_seconds")
UPLOAD_DIR = config.get("app", "upload_dir")
SCHEME_DEADLINE = datetime.strptime(config.get("pension", "scheme_deadline"),
                                    "%Y-%m-%d %H:%M")
MIN_AGE = config.getint("pension", "min_age")
SLA_DAYS = config.getint("pension", "sla_days")
AI_CHATBOT_API_URL = os.environ.get("SEWASETU_AI_API_URL", "").strip()
AI_CHATBOT_API_KEY = os.environ.get("SEWASETU_AI_API_KEY", "").strip()
AI_CHATBOT_MODEL = os.environ.get("SEWASETU_AI_MODEL", "").strip()

BLOCKS = ["Sonari", "Rajapara", "Dhemaji Pathar", "Borgaon", "Namti", "Khelua"]

import logging
_logdir = "/var/log/sewasetu"
try:
    os.makedirs(_logdir, exist_ok=True)
    _fh = logging.FileHandler(os.path.join(_logdir, "sewasetu-app.log"))
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s in app: %(message)s"))
    app.logger.addHandler(_fh)
    app.logger.setLevel(logging.INFO)
except Exception:
    pass


def get_db():
    if has_app_context():
        conn = g.get("db_conn")
        if conn is not None and not conn.closed:
            return conn

    conn = connect_db()
    if has_app_context():
        g.db_conn = conn
    return conn


@app.teardown_appcontext
def close_db(error=None):
    conn = g.pop("db_conn", None)
    if conn is not None and not conn.closed:
        conn.close()


def sanitize(value, maxlen=100):
    if value is None:
        return ""
    return value.strip()[:maxlen]


def hash_password(p):
    return hashlib.sha256(p.encode("utf-8")).hexdigest()


def send_sms(mobile, text):
    try:
        requests.post(SMS_GATEWAY_URL + "/api/send",
                      json={"to": mobile, "text": text}, timeout=5)
    except Exception as e:
        app.logger.error("sms gateway error: %s" % e)


def deadline_remaining():
    delta = SCHEME_DEADLINE - datetime.now()
    if delta.total_seconds() <= 0:
        return None
    return int(delta.total_seconds() // 3600)


def new_application_no():
    return "SSP" + datetime.now().strftime("%y") + str(random.randint(100000, 999999))


def chatbot_configured():
    return bool(AI_CHATBOT_API_URL and AI_CHATBOT_API_KEY and AI_CHATBOT_MODEL)


def chatbot_url_is_secure():
    try:
        parsed_url = urlsplit(AI_CHATBOT_API_URL)
        return (parsed_url.scheme == "https" or
                (parsed_url.scheme == "http" and parsed_url.hostname in
                 ("localhost", "127.0.0.1", "::1")))
    except ValueError:
        return False


@app.context_processor
def inject_chatbot_config():
    return {"chatbot_enabled": chatbot_configured()}


def safe_document_path(path):
    if not path:
        return None
    upload_dir = os.path.realpath(UPLOAD_DIR)
    document_path = os.path.realpath(path)
    try:
        if os.path.commonpath((upload_dir, document_path)) != upload_dir:
            return None
    except ValueError:
        return None
    if not os.path.isfile(document_path):
        return None
    return document_path


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", hours_left=deadline_remaining())


@app.route("/about")
def about():
    return render_template("about.html")


@app.route("/api/chatbot", methods=["POST"])
def chatbot():
    if not chatbot_configured():
        return jsonify(error="The AI assistant is not configured yet."), 503
    if not chatbot_url_is_secure():
        app.logger.error("AI chatbot provider URL must use HTTPS.")
        return jsonify(error="The AI assistant is temporarily unavailable."), 503

    payload = request.get_json(silent=True)
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str) or not message.strip():
        return jsonify(error="Enter a question to continue."), 400
    message = message.strip()
    if len(message) > 1000:
        return jsonify(error="Please keep your question under 1,000 characters."), 400

    language = payload.get("language", "English")
    if language not in LANGUAGES:
        return jsonify(error="Choose English, Hindi, or Assamese."), 400

    try:
        answer, sources = answer_question(
            message,
            language,
            url=AI_CHATBOT_API_URL,
            api_key=AI_CHATBOT_API_KEY,
            model=AI_CHATBOT_MODEL,
            post=requests.post,
        )
    except requests.RequestException:
        app.logger.exception("AI chatbot provider request failed.")
        return jsonify(error="The AI assistant couldn't respond just now. Please try again later."), 502
    except ValueError:
        app.logger.exception("AI chatbot provider returned an invalid response.")
        return jsonify(error="The AI assistant couldn't respond just now. Please try again later."), 502
    return jsonify(reply=answer, sources=sources)


@app.route("/__gateway/", defaults={"subpath": ""})
@app.route("/__gateway/<path:subpath>")
def gateway_proxy(subpath):
    try:
        r = requests.get(SMS_GATEWAY_URL + "/" + subpath,
                         params=request.args, timeout=5)
        return (r.content, r.status_code,
                {"Content-Type": r.headers.get("Content-Type", "text/html")})
    except Exception as e:
        return ("SMS gateway unreachable: %s" % e, 502)


# ---------------------------------------------------------------------------
# Application flow: mobile -> OTP -> form steps -> upload -> declaration
# ---------------------------------------------------------------------------

@app.route("/apply", methods=["GET", "POST"])
def apply():
    if deadline_remaining() is None:
        flash("The application window for this scheme has closed.")
        return redirect(url_for("index"))
    if request.method == "POST":
        mobile = request.form.get("mobile", "").strip()
        captcha = request.form.get("captcha", "").strip()
        if (len(mobile) != 10 or not mobile.isdigit()
                or captcha != str(session.get("captcha_answer", ""))):
            flash("Something went wrong. Please try again.")
            return render_template("apply.html", captcha_q=make_captcha())
        code = f"{secrets.randbelow(900000) + 100000:06d}"
        conn = get_db()
        cur = conn.cursor()
        cur.execute("INSERT INTO otps (mobile, code, created_at) VALUES (%s, %s, %s)",
                    (mobile, code, datetime.now()))
        conn.commit()
        cur.close(); conn.close()
        send_sms(mobile, "Your Sewa Setu OTP is %s. Valid for 5 minutes." % code)
        session.permanent = True
        session["apply_mobile"] = mobile
        session.pop("captcha_answer", None)
        return redirect(url_for("verify"))
    return render_template("apply.html", captcha_q=make_captcha())


def make_captcha():
    a, b = random.randint(1, 9), random.randint(1, 9)
    session["captcha_answer"] = a + b
    return "%d + %d" % (a, b)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    mobile = session.get("apply_mobile")
    if not mobile:
        return redirect(url_for("apply"))
    if request.method == "POST":
        code = request.form.get("otp", "").strip()
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT code, created_at FROM otps WHERE mobile = %s "
                    "ORDER BY id DESC LIMIT 1", (mobile,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0] == code:
            age = (datetime.now() - row[1]).total_seconds()
            if age > OTP_VALIDITY_SECONDS:
                app.logger.warning("otp expired mobile=%s age=%ds" % (mobile, int(age)))
                flash("OTP expired. Please request a new OTP.")
                return redirect(url_for("apply"))
            session["verified_mobile"] = mobile
            return redirect(url_for("form_step", step=1))
        flash("Invalid OTP.")
    return render_template("verify.html", mobile=mobile)


@app.route("/form/<int:step>", methods=["GET", "POST"])
def form_step(step):
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if step not in (1, 2, 3):
        abort(404)
    if request.method == "POST":
        data = session.get("form_data", {})
        for k, v in request.form.items():
            data[k] = v
        session["form_data"] = data
        if step < 3:
            return redirect(url_for("form_step", step=step + 1))
        return redirect(url_for("upload"))
    return render_template("form_step%d.html" % step,
                           data=session.get("form_data", {}), blocks=BLOCKS)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        if request.form.get("confirm_review") == "1":
            if (request.form.get("reviewed") != "yes"
                    or not session.get("doc_path")
                    or not session.get("ocr_review_pending")):
                flash("Please upload and review your age-proof document first.")
                return redirect(url_for("upload"))
            session.pop("ocr_review_pending", None)
            session.pop("ocr_suggestions", None)
            return redirect(url_for("declaration"))

        f = request.files.get("document")
        if f is None or f.filename == "":
            flash("Please choose a PDF or JPG age-proof document.")
            return render_template("upload.html")
        filename = f.filename.lower()
        extension = os.path.splitext(filename)[1]
        if extension not in (".pdf", ".jpg", ".jpeg"):
            flash("Please choose a PDF or JPG age-proof document.")
            return render_template("upload.html")
        content = f.read(MAX_UPLOAD_BYTES + 1)
        valid_content = (
            extension == ".pdf" and content.startswith(b"%PDF-")
        ) or (
            extension in (".jpg", ".jpeg")
            and content.startswith(b"\xff\xd8\xff")
        )
        if not valid_content:
            flash("The selected file does not match its PDF or JPG format.")
            return render_template("upload.html")
        if len(content) > MAX_UPLOAD_BYTES:
            flash("The document must be 5 MB or smaller.")
            return render_template("upload.html")
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        path = os.path.join(
            UPLOAD_DIR,
            f"{session['verified_mobile']}_{uuid.uuid4().hex}{extension}",
        )
        with open(path, "wb") as out:
            out.write(content)
        session["doc_path"] = path
        session["ocr_review_pending"] = True
        try:
            session["ocr_suggestions"] = extract_age_proof_suggestions(
                content, extension
            )
            ocr_hints = applicant_document_hints(
                session.get("form_data", {}),
                session["ocr_suggestions"],
            )
            ocr_error = ""
        except OCRUnavailableError:
            app.logger.exception("Local OCR could not process an age-proof upload.")
            session["ocr_suggestions"] = {"name": "", "dates": []}
            ocr_hints = []
            ocr_error = (
                "Automatic reading was unavailable. Please compare the document "
                "with the details you entered yourself."
            )
        return render_template(
            "upload.html",
            ocr_suggestions=session["ocr_suggestions"],
            ocr_error=ocr_error,
            ocr_hints=ocr_hints,
        )
    return render_template(
        "upload.html",
        ocr_suggestions=session.get("ocr_suggestions"),
        ocr_error="",
        ocr_hints=applicant_document_hints(
            session.get("form_data", {}),
            session.get("ocr_suggestions", {}),
        ),
    )


@app.route("/declaration", methods=["GET", "POST"])
def declaration():
    if not session.get("verified_mobile"):
        flash("Session expired. Please verify your mobile number again.")
        return redirect(url_for("apply"))
    if request.method == "POST":
        return handle_submission()
    return render_template("declaration.html")


def handle_submission():
    mobile = session.get("verified_mobile")
    data = session.get("form_data", {})
    doc_path = session.get("doc_path", "")

    name = sanitize(data.get("applicant_name", ""))
    village = data.get("village", "").strip()
    block = data.get("block", "").strip()
    gender = data.get("gender", "")
    marital = data.get("marital_status", "")
    husband_name = data.get("husband_name", "")
    husband_employer = data.get("husband_employer", "")
    bank_account = data.get("bank_account", "").strip()
    ifsc = data.get("ifsc", "").strip().upper()
    dob_raw = data.get("dob", "").strip()

    if not name or not dob_raw:
        flash("Something went wrong. Please try again.")
        return redirect(url_for("form_step", step=1))
    if not village or not block:
        flash("Please complete your village and block details.")
        return redirect(url_for("form_step", step=2))
    if not bank_account or not ifsc:
        flash("Please complete your bank details.")
        return redirect(url_for("form_step", step=3))

    if gender not in ("Male", "Female", "Other"):
        flash("Please select a valid gender.")
        return redirect(url_for("form_step", step=1))
    if marital not in ("Married", "Unmarried", "Widowed"):
        flash("Please select a valid marital status.")
        return redirect(url_for("form_step", step=1))
    if block not in BLOCKS:
        flash("Please select a valid block.")
        return redirect(url_for("form_step", step=2))
    if not bank_account.isdigit() or not 9 <= len(bank_account) <= 18:
        flash("Enter a valid bank account number using 9 to 18 digits.")
        return redirect(url_for("form_step", step=3))
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", ifsc):
        flash("Enter a valid 11-character IFSC code.")
        return redirect(url_for("form_step", step=3))

    if marital == "Widowed":
        if not husband_name or not husband_employer:
            flash("Something went wrong. Please try again.")
            return redirect(url_for("form_step", step=1))

    dob = None
    for fmt in ("%m/%d/%Y", "%d/%m/%Y"):
        try:
            dob = datetime.strptime(dob_raw, fmt).date()
            break
        except ValueError:
            continue
    if dob is None:
        flash("Something went wrong. Please try again.")
        return redirect(url_for("form_step", step=1))

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < MIN_AGE:
        flash("Applicant must be at least %d years of age to be eligible." % MIN_AGE)
        return redirect(url_for("form_step", step=1))

    if datetime.now() > SCHEME_DEADLINE:
        flash("The application deadline has passed.")
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT count(*) FROM applications WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] > 0:
        cur.close(); conn.close()
        flash("An application already exists for this mobile number. "
              "Duplicate applications are not permitted.")
        return redirect(url_for("index"))

    app_no = new_application_no()
    cur.execute(
        """INSERT INTO applications
           (application_no, applicant_name, mobile, dob, gender, marital_status,
            husband_name, husband_employer, village, block, bank_account, ifsc,
            doc_path, status, submitted_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'PENDING',%s)
           RETURNING id""",
        (app_no, name, mobile, dob, gender, marital, husband_name,
         husband_employer, village, block, bank_account, ifsc, doc_path,
         datetime.now()))
    new_id = cur.fetchone()[0]

    portal_pass = dob.strftime("%d%m%Y")
    cur.execute("SELECT count(*) FROM portal_users WHERE mobile = %s", (mobile,))
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO portal_users (mobile, password_hash) VALUES (%s,%s)",
                    (mobile, hash_password(portal_pass)))
    conn.commit()

    app.logger.info("generating acknowledgment pdf application=%d" % new_id)
    pdf_bytes = generate_acknowledgment(cur, new_id)
    ack_dir = os.path.join(UPLOAD_DIR, "ack")
    os.makedirs(ack_dir, exist_ok=True)
    with open(os.path.join(ack_dir, "%d.pdf" % new_id), "wb") as out:
        out.write(pdf_bytes)

    cur.close(); conn.close()
    session.pop("form_data", None)
    session.pop("doc_path", None)
    session.pop("verified_mobile", None)
    send_sms(mobile, "Sewa Setu: application %s received. Track at the status portal "
                     "with mobile no. and password (DOB as DDMMYYYY)." % app_no)
    return render_template("confirmation.html", app_no=app_no, app_id=new_id)


def generate_acknowledgment(cur, app_id):
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, submitted_at, status FROM applications WHERE id = %s",
                (app_id,))
    row = cur.fetchone()
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "GOVERNMENT OF PURVANCHAL", ln=1, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, "Department of Social Welfare", ln=1, align="C")
    pdf.cell(0, 8, "Old Age Pension Scheme - Acknowledgment", ln=1, align="C")
    pdf.ln(4)
    
    # OPTIMIZED: Replaced the heavy 2000-iteration loop with a clean vector box
    pdf.rect(10, 10, 190, 277)

    labels = ["Application No", "Applicant Name", "Mobile", "Date of Birth",
              "Village", "Block", "Bank Account", "IFSC", "Submitted At", "Status"]
    if os.path.isfile(PDF_UNICODE_FONT):
        pdf.add_font("NotoDevanagari", "", PDF_UNICODE_FONT)
        table_font = "NotoDevanagari"
    else:
        table_font = "Helvetica"
    pdf.set_font(table_font, "", 10)
    for label, val in zip(labels, row):
        pdf.cell(60, 8, label, border=1)
        pdf.cell(0, 8, str(val), border=1, ln=1)
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, "This is a computer generated acknowledgment. Processing SLA "
                         "as per the Purvanchal Right to Public Services Act applies.")
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# Status portal (citizen login)
# ---------------------------------------------------------------------------

@app.route("/status", methods=["GET", "POST"])
def status_login():
    if request.method == "POST":
        mobile = request.form.get("mobile", "").strip()
        password = request.form.get("password", "")
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT password_hash FROM portal_users WHERE mobile = %s", (mobile,))
        row = cur.fetchone()
        cur.close(); conn.close()
        if row and row[0] == hash_password(password):
            session.clear()
            session["logged_in"] = True
            session["portal_mobile"] = mobile
            conn = get_db()
            cur = conn.cursor()
            cur.execute("SELECT id FROM applications WHERE mobile = %s "
                        "ORDER BY submitted_at DESC LIMIT 1", (mobile,))
            r = cur.fetchone()
            cur.close(); conn.close()
            if r:
                return redirect(url_for("view_application", app_id=r[0]))
            flash("No application found for this mobile number.")
            return redirect(url_for("status_login"))
        flash("Something went wrong. Please try again.")
    return render_template("status_login.html")


@app.route("/application/<int:app_id>")
def view_application(app_id):
    if not session.get("logged_in"):
        flash("Please login to view application status.")
        return redirect(url_for("status_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, village, block, "
                "bank_account, ifsc, status, submitted_at, decided_at "
                "FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        abort(404)
    if session.get("admin") is not True and row[2] != session.get("portal_mobile"):
        abort(404)
    status_guidance = application_status_guidance(
        row[8], row[9], row[10]
    )
    return render_template(
        "application.html",
        a=row,
        app_id=app_id,
        status_guidance=status_guidance,
    )


@app.route("/admin/application/<int:app_id>/document")
def admin_document(app_id):
    if session.get("admin") is not True:
        abort(403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT doc_path FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    document_path = safe_document_path(row[0] if row else None)
    if not document_path:
        abort(404)
    return send_file(
        document_path,
        as_attachment=True,
        download_name=os.path.basename(document_path),
    )


@app.route("/health")
def health():
    try:
        cur = get_db().cursor()
        cur.execute("SELECT 1")
        cur.fetchone()
        cur.close()
    except psycopg2.Error:
        app.logger.exception("Portal database health check failed")
        return jsonify(status="unavailable"), 503
    return jsonify(status="ok")


@app.route("/live")
def live():
    return jsonify(status="ok")


@app.route("/ack/<int:app_id>.pdf")
def ack_pdf(app_id):
    if not session.get("logged_in") and session.get("admin") is not True:
        abort(403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, mobile FROM applications WHERE id = %s", (app_id,))
    application = cur.fetchone()
    if not application:
        cur.close(); conn.close()
        abort(404)
    if (session.get("admin") is not True
            and application[1] != session.get("portal_mobile")):
        cur.close(); conn.close()
        abort(404)

    path = os.path.join(UPLOAD_DIR, "ack", "%d.pdf" % app_id)
    if not os.path.exists(path):
        data = generate_acknowledgment(cur, app_id)
        cur.close(); conn.close()
        return send_file(io.BytesIO(data), mimetype="application/pdf")
    cur.close(); conn.close()
    return send_file(path, mimetype="application/pdf")


@app.route("/status/reset", methods=["GET", "POST"])
def reset_password():
    if request.method == "POST":
        mobile = request.form.get("mobile", "")
        conn = get_db()
        cur = conn.cursor()
        # FIXED: Parameterized query to prevent SQL Injection
        cur.execute("SELECT mobile FROM portal_users WHERE mobile = %s", (mobile,))
        row = cur.fetchone()
        if row:
            cur.execute("SELECT dob FROM applications WHERE mobile = %s LIMIT 1", (mobile,))
            r2 = cur.fetchone()
            if r2:
                newpass = r2[0].strftime("%d%m%Y")
                cur.execute("UPDATE portal_users SET password_hash = %s WHERE mobile = %s",
                            (hash_password(newpass), row[0]))
                conn.commit()
                send_sms(row[0], "Sewa Setu: your password has been reset to your "
                                 "date of birth (DDMMYYYY).")
        cur.close(); conn.close()
        flash("If the mobile number exists, the password has been reset and sent by SMS.")
    return render_template("reset.html")


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if (ADMIN_USERNAME and ADMIN_PASSWORD
                and request.form.get("username") == ADMIN_USERNAME and
                request.form.get("password") == ADMIN_PASSWORD):
            session.clear()
            session["logged_in"] = True
            session["admin"] = True
            session.pop("portal_mobile", None)
            return redirect(url_for("admin_dashboard"))
        flash("Invalid credentials.")
    return render_template("admin_login.html")


@app.route("/admin/dashboard")
def admin_dashboard():
    if session.get("admin") is not True:
        return redirect(url_for("admin_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM applications GROUP BY status")
    by_status = cur.fetchall()
    cur.execute("SELECT count(*) FROM applications WHERE status = 'PENDING' "
                "AND submitted_at < %s", (datetime.now() - timedelta(days=SLA_DAYS),))
    overdue = cur.fetchone()[0]
    cur.execute("SELECT block, count(*) FROM applications WHERE status = 'PENDING' "
                "GROUP BY block ORDER BY count(*) DESC")
    by_block = cur.fetchall()
    cur.close(); conn.close()
    insights = operations_insights(by_status, overdue, by_block)
    return render_template("admin_dashboard.html", by_status=by_status,
                           overdue=overdue, by_block=by_block, sla=SLA_DAYS,
                           insights=insights)


@app.route("/admin/applications")
def admin_list():
    if session.get("admin") is not True:
        return redirect(url_for("admin_login"))
    status = request.args.get("status", "PENDING")
    page = int(request.args.get("page", 1))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT id, application_no, applicant_name, mobile, block, status, "
                "submitted_at FROM applications WHERE status = %s "
                "ORDER BY submitted_at ASC LIMIT 50 OFFSET %s",
                (status, (page - 1) * 50))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return render_template("admin_list.html", rows=rows, status=status, page=page)


@app.route("/admin/application/<int:app_id>")
def admin_view(app_id):
    if session.get("admin") is not True:
        return redirect(url_for("admin_login"))
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT application_no, applicant_name, mobile, dob, gender, "
                "marital_status, village, block, bank_account, ifsc, doc_path, "
                "status, submitted_at, decided_at, decided_by "
                "FROM applications WHERE id = %s", (app_id,))
    row = cur.fetchone()
    cur.close(); conn.close()
    if not row:
        abort(404)
    ocr_suggestions = None
    ocr_message = ""
    if row[10]:
        document_path = safe_document_path(row[10])
        if document_path:
            extension = os.path.splitext(document_path)[1].lower()
            try:
                if os.path.getsize(document_path) > MAX_UPLOAD_BYTES:
                    raise OCRUnavailableError(
                        "The stored document is too large for local OCR."
                    )
                with open(document_path, "rb") as document:
                    content = document.read(MAX_UPLOAD_BYTES + 1)
                ocr_suggestions = extract_age_proof_suggestions(
                    content, extension
                )
            except (OSError, OCRUnavailableError):
                app.logger.exception(
                    "Staff review local OCR failed for application id=%d", app_id
                )
                ocr_message = "Local OCR was unavailable for this document."
        else:
            ocr_message = "The stored document was not available to local OCR."
    review_assistance = staff_review_assistance(
        row, ocr_suggestions=ocr_suggestions, ocr_message=ocr_message
    )
    return render_template(
        "admin_view.html",
        a=row,
        app_id=app_id,
        review_assistance=review_assistance,
    )


@app.route("/admin/approve/<int:app_id>", methods=["POST"])
def admin_approve(app_id):
    # FIXED: Enforce strict admin session check instead of regular user login
    if session.get("admin") is not True:
        abort(403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE applications SET status = 'APPROVED', decided_at = %s "
                "WHERE id = %s", (datetime.now(), app_id))
    conn.commit()
    cur.close(); conn.close()
    flash("Application approved.")
    return redirect(url_for("admin_list"))


@app.route("/admin/reject/<int:app_id>", methods=["POST"])
def admin_reject(app_id):
    # FIXED: Enforce strict admin session check instead of regular user login
    if session.get("admin") is not True:
        abort(403)
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE applications SET status = 'REJECTED', decided_at = %s "
                "WHERE id = %s", (datetime.now(), app_id))
    conn.commit()
    cur.close(); conn.close()
    flash("Application rejected.")
    return redirect(url_for("admin_list"))


# ---------------------------------------------------------------------------
# Unused / legacy
# ---------------------------------------------------------------------------

def export_to_excel(rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["application_no", "name", "account", "ifsc", "amount"])
    for r in rows:
        writer.writerow(list(r) + ["250"])
    return out.getvalue()


def old_payment_gateway_callback(txn):
    status = txn.get("STATUS")
    if status == "0300":
        return "SUCCESS"
    elif status == "0399":
        return "FAILED"
    return "PENDING"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)