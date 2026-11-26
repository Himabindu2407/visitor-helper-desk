from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, send_file
from dotenv import load_dotenv
from datetime import datetime, timedelta
import secrets, time, sqlite3, csv, io, os, re, shutil, tempfile, threading
from PIL import Image, ImageOps, UnidentifiedImageError
from contextlib import closing
Image.MAX_IMAGE_PIXELS = 16000000
os.umask(0o077)

load_dotenv()

app = Flask(__name__, static_folder="public/static", static_url_path="/static")
# Phase 6.6.1: explicit environment separation.
VHD_ENV = os.environ.get("VHD_ENV", "development").strip().lower()
if VHD_ENV not in {"development", "production"}:
    raise RuntimeError("VHD_ENV must be either 'development' or 'production'.")
IS_VERCEL = os.environ.get("VERCEL") == "1"
IS_PRODUCTION = IS_VERCEL or VHD_ENV == "production"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
CLOUD = bool(DATABASE_URL)
if (IS_VERCEL or VHD_ENV == "production") and not CLOUD:
    raise RuntimeError("Configure DATABASE_URL before running this cloud deployment.")
if CLOUD:
    import psycopg
    DB_ERRORS = (sqlite3.Error, psycopg.Error)
else:
    DB_ERRORS = (sqlite3.Error,)
SECRET_KEY = os.environ.get("VHD_SECRET_KEY", "").strip()
if IS_PRODUCTION and (len(SECRET_KEY) < 32 or SECRET_KEY.lower().startswith(("change", "replace", "example")) or len(set(SECRET_KEY)) < 12):
    raise RuntimeError("Production requires a strong VHD_SECRET_KEY of at least 32 characters.")
app.secret_key = SECRET_KEY or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024  # Selfie upload request limit
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure cookies are enforced in production; local HTTP testing stays usable in development.
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION or os.environ.get("VHD_COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True

# Phase 6.6.4: host allow-list. Add LAN hostname/IP through VHD_ALLOWED_HOSTS.
DEFAULT_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
VHD_ALLOWED_HOSTS = DEFAULT_ALLOWED_HOSTS | {h.strip().lower() for h in os.environ.get("VHD_ALLOWED_HOSTS", "").split(",") if h.strip()}
VHD_ALLOWED_HOSTS.update(os.environ[k].lower() for k in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_BRANCH_URL") if os.environ.get(k))
app.config["TRUSTED_HOSTS"] = sorted(VHD_ALLOWED_HOSTS)
if IS_PRODUCTION:
    configured_admin = os.environ.get("VHD_ADMIN_PASSWORD", "").strip()
    if len(configured_admin) < 12 or configured_admin.lower().startswith(("change", "replace", "example")):
        raise RuntimeError("Production requires a configured VHD_ADMIN_PASSWORD (minimum 12 characters).")

# Prevent browser caching of admin pages so Back-button navigation after logout
# cannot display stale authenticated content. Server-side routes still verify session.
@app.after_request
def apply_admin_no_cache(response):
    if request.path.startswith("/admin") or request.path == "/" or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["Vary"] = "Cookie"
    return response

@app.before_request
def validate_request_host():
    # Reject unexpected Host headers. Do not trust X-Forwarded-Host without a
    # correctly configured reverse proxy and ProxyFix deployment.
    raw_host = (request.host or "").strip().lower()
    if raw_host.startswith("[") and "]" in raw_host:
        host = raw_host[1:raw_host.index("]")]
    else:
        host = raw_host.split(":", 1)[0]
    if host not in VHD_ALLOWED_HOSTS:
        app.logger.warning("Rejected request with untrusted Host header")
        return "Invalid host header.", 400


@app.after_request
def apply_security_headers(response):
    # Phase 6.6.2: CSP supports the app's inline template styles, but scripts remain self-only.
    response.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'self'; frame-ancestors 'none'")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if IS_PRODUCTION:
        # HSTS only when production is deployed behind HTTPS.
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.errorhandler(413)
def too_large(_error):
    return jsonify(message="Request exceeds allowed size."), 413

@app.errorhandler(404)
def safe_not_found(_error):
    return "The requested page was not found.", 404


@app.errorhandler(500)
def safe_internal_error(_error):
    app.logger.exception("Unhandled application error")
    return "An unexpected server error occurred. Please try again later.", 500

OTP_TTL_SECONDS = 300
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN_SECONDS = 30
DATA_DIR = os.path.abspath(os.environ.get("VHD_DATA_DIR", os.path.dirname(__file__)))
if not CLOUD:
    os.makedirs(DATA_DIR, mode=0o700, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "visitor_records.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
SELFIE_DIR = os.path.join(DATA_DIR, "private_uploads", "selfies")
MAX_SELFIE_BYTES = 3 * 1024 * 1024
BACKUP_RETENTION_DAYS = int(os.environ.get("VHD_BACKUP_RETENTION_DAYS", "30"))
AUDIT_RETENTION_DAYS = int(os.environ.get("VHD_AUDIT_RETENTION_DAYS", "180"))
MAX_RESTORE_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

ADMIN_PASSWORD = os.environ.get("VHD_ADMIN_PASSWORD", "change-this-admin-password")
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
_login_failures = {}  # in-memory, per-IP rate limit for local deployment

# Public registration abuse control (in-memory/local deployment).
REGISTRATION_LIMIT = 10
REGISTRATION_WINDOW_SECONDS = 900  # 15 minutes
_registration_events = {}

def client_ip():
    # Do not trust forwarded headers in this local deployment.
    if IS_VERCEL:
        import ipaddress
        try: return str(ipaddress.ip_address(request.headers.get("X-Forwarded-For", "").strip()))
        except ValueError: return "unknown"
    return request.remote_addr or "unknown"

def _prune_events(events, ip, now, window_seconds):
    recent = [stamp for stamp in events.get(ip, []) if now - stamp < window_seconds]
    if recent:
        events[ip] = recent
    else:
        events.pop(ip, None)
    return recent

_rate_lock = threading.Lock()
def rate_limit_status(events, ip, now, limit, window_seconds):
    if CLOUD:
        scope = "login" if events is _login_failures else "registration"
        with closing(get_db()) as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (scope + ":" + ip,))
            conn.execute("DELETE FROM rate_events WHERE scope=? AND ip=? AND stamp<=?", (scope, ip, now-window_seconds))
            count, oldest = conn.execute("SELECT COUNT(*), MIN(stamp) FROM rate_events WHERE scope=? AND ip=?", (scope, ip)).fetchone()
            if count >= limit:
                conn.commit()
                return False, max(1, int(window_seconds-(now-oldest)))
            conn.execute("INSERT INTO rate_events(scope,ip,stamp) VALUES (?,?,?)", (scope,ip,now))
            conn.commit()
            return True, 0
    with _rate_lock:
        for key in list(events):
            _prune_events(events, key, now, window_seconds)
        if len(events) >= 10000 and ip not in events:
            return False, window_seconds
        return _rate_limit_locked(events, ip, now, limit, window_seconds)

def _rate_limit_locked(events, ip, now, limit, window_seconds):
    recent = _prune_events(events, ip, now, window_seconds)
    if len(recent) >= limit:
        retry_after = max(1, int(window_seconds - (now - recent[0])))
        return False, retry_after
    recent.append(now)
    events[ip] = recent
    return True, 0


VISITOR_TYPES = {"Student", "College Faculty / HOD", "Corporate Visitor", "Job Candidate", "Internship Candidate", "Vendor", "Guest", "Other"}
FIELD_LIMITS = {"fullName": 100, "mobile": 10, "visitorType": 40, "organization": 150, "email": 150, "personToMeet": 100, "purpose": 250, "notes": 500}
EMAIL_RE = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")

def clean_text(value, limit):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.strip()
    if len(value) > limit:
        raise ValueError("One or more fields exceed the allowed length.")
    if any(ord(ch) < 32 and ch not in "\t\n\r" for ch in value):
        raise ValueError("Invalid control characters in input.")
    return value

def validate_registration(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid request payload.")
    values = {key: clean_text(data.get(key, ""), limit) for key, limit in FIELD_LIMITS.items()}
    if len(values["fullName"]) < 2 or not re.search(r"[A-Za-z\u00c0-\u024f]", values["fullName"]):
        raise ValueError("Enter a valid full name (at least 2 characters).")
    if not re.fullmatch(r"[6-9]\d{9}", values["mobile"]):
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    if values["visitorType"] not in VISITOR_TYPES:
        raise ValueError("Select a valid visitor type.")
    if values["email"] and not EMAIL_RE.fullmatch(values["email"]):
        raise ValueError("Enter a valid email address.")
    if len(values["personToMeet"]) < 2:
        raise ValueError("Enter the person or department to meet.")
    if not values["purpose"]:
        raise ValueError("Enter the purpose of visit.")
    # Reject script/markup-like input in fields intended to be plain text.
    if any("<" in value or ">" in value for key, value in values.items() if key != "notes"):
        raise ValueError("Please remove HTML or markup characters from the form fields.")
    return values

def require_public_csrf():
    expected = session.get("public_csrf", "")
    submitted = request.headers.get("X-CSRF-Token", "")
    return bool(expected and submitted and secrets.compare_digest(submitted.encode(), expected.encode()))

class HybridRow(tuple):
    def __new__(cls, values, names):
        obj = super().__new__(cls, values)
        obj.names = names
        return obj
    def __getitem__(self, key):
        return super().__getitem__(self.names.index(key) if isinstance(key, str) else key)
    def __getattr__(self, key):
        if key in self.names: return self[key]
        raise AttributeError(key)

def hybrid_row(cursor):
    names = [c.name for c in cursor.description] if cursor.description else []
    return lambda values: HybridRow(values, names)

class CloudConnection:
    def __init__(self):
        self.conn = psycopg.connect(DATABASE_URL, row_factory=hybrid_row, connect_timeout=10, sslmode="require", prepare_threshold=None)
    def execute(self, sql, params=()):
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
        sql = sql.replace("date(registered_at)", "CAST(LEFT(registered_at,10) AS DATE)").replace("date(created_at)", "CAST(LEFT(created_at,10) AS DATE)").replace("date(?)", "CAST(? AS DATE)")
        return self.conn.execute(sql.replace("?", "%s"), params)
    def commit(self): self.conn.commit()
    def close(self): self.conn.close()

def get_db():
    if CLOUD: return CloudConnection()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.row_factory = sqlite3.Row
    return conn


# Phase 6.5 audit logging. Never store passwords, OTPs, session tokens, or form PII.
def audit_log(event, category="activity", details="", actor=None):
    try:
        with closing(get_db()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'activity', details TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'visitor', ip_address TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_logs(category)")
            conn.execute("INSERT INTO audit_logs (event, category, details, actor, ip_address, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (str(event)[:100], str(category)[:40], str(details)[:500], actor or ("admin" if session.get("admin_authenticated") else "visitor"), client_ip(), datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")))
            conn.commit()
    except Exception:
        app.logger.exception("Audit log write failed for event %s", event)


def _ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def create_database_backup(prefix="visitor_records"):
    """Create a consistent SQLite backup using the SQLite backup API."""
    if not os.path.exists(DB_PATH):
        return None
    _ensure_backup_dir()
    stamp = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d_%H%M%S")
    target = os.path.join(BACKUP_DIR, f"{prefix}_{stamp}_{secrets.token_hex(2)}.sqlite3")
    source_conn = sqlite3.connect(DB_PATH)
    backup_conn = sqlite3.connect(target)
    try:
        source_conn.backup(backup_conn)
        backup_conn.commit()
    finally:
        backup_conn.close()
        source_conn.close()
    # Verify the backup before treating it as successful.
    check_conn = sqlite3.connect(target)
    try:
        check = check_conn.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise RuntimeError("Created backup failed SQLite integrity_check.")
    finally:
        check_conn.close()
    return target


def prune_old_backups():
    """Delete only backup files older than the configured retention period."""
    _ensure_backup_dir()
    cutoff = time.time() - max(1, BACKUP_RETENTION_DAYS) * 86400
    removed = 0
    for name in os.listdir(BACKUP_DIR):
        if not name.endswith(".sqlite3") or not name.startswith("visitor_records_"):
            continue
        path = os.path.join(BACKUP_DIR, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            app.logger.warning("Could not prune backup file: %s", name)
    return removed


def database_integrity_status():
    try:
        with closing(get_db()) as conn:
            result = conn.execute("SELECT 1" if CLOUD else "PRAGMA integrity_check").fetchone()
            if CLOUD: return "connected" if result else "unknown"
        return result[0] if result else "unknown"
    except DB_ERRORS:
        app.logger.exception("Database integrity check failed")
        return "error"


def validate_restore_database(path):
    """Reject non-SQLite, corrupt, or incompatible visitor databases."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA trusted_schema=OFF")
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise ValueError("Database integrity check failed.")
        table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='visitors'").fetchone()
        if not table:
            raise ValueError("Backup does not contain the visitors table.")
        required = {"id", "visitor_id", "full_name", "mobile", "visitor_type", "organization", "email", "person_to_meet", "purpose", "notes", "mobile_verified", "registered_at"}
        actual = {row[1] for row in conn.execute("PRAGMA table_info(visitors)").fetchall()}
        if not required.issubset(actual):
            raise ValueError("Backup schema is incompatible with Visitor Helper Desk.")
    finally:
        conn.close()


def init_db():
    with closing(get_db()) as conn:
        if CLOUD: conn.execute("SELECT pg_advisory_xact_lock(7142901)")
        conn.execute("""CREATE TABLE IF NOT EXISTS visitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visitor_id TEXT UNIQUE NOT NULL,
            full_name TEXT NOT NULL,
            mobile TEXT NOT NULL,
            visitor_type TEXT NOT NULL,
            organization TEXT,
            email TEXT,
            person_to_meet TEXT NOT NULL,
            purpose TEXT NOT NULL,
            notes TEXT,
            mobile_verified INTEGER NOT NULL DEFAULT 1,
            registered_at TEXT NOT NULL
        )""")
        if CLOUD:
            conn.execute("ALTER TABLE visitors ADD COLUMN IF NOT EXISTS selfie_filename TEXT")
            conn.execute("ALTER TABLE visitors ADD COLUMN IF NOT EXISTS selfie_bytes BYTEA")
            conn.execute("CREATE TABLE IF NOT EXISTS rate_events (scope TEXT NOT NULL, ip TEXT NOT NULL, stamp DOUBLE PRECISION NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_scope_ip ON rate_events(scope,ip,stamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_stamp ON rate_events(stamp)")
        else:
            visitor_columns = {row[1] for row in conn.execute("PRAGMA table_info(visitors)").fetchall()}
            if "selfie_filename" not in visitor_columns:
                conn.execute("ALTER TABLE visitors ADD COLUMN selfie_filename TEXT")
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, category TEXT NOT NULL DEFAULT 'activity',
            details TEXT NOT NULL DEFAULT '', actor TEXT NOT NULL DEFAULT 'visitor', ip_address TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_logs(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_category ON audit_logs(category)")
        conn.commit()


def create_visitor(data, selfie_filename=None, selfie_bytes=None):
    stamp = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).isoformat(timespec="seconds")
    visitor_id = f"VHD-{datetime.now(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    with closing(get_db()) as conn:
        conn.execute("""INSERT INTO visitors (visitor_id, full_name, mobile, visitor_type, organization, email,
            person_to_meet, purpose, notes, mobile_verified, registered_at, selfie_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (visitor_id, data.get("fullName", ""), data.get("mobile", ""), data.get("visitorType", ""),
             data.get("organization", ""), data.get("email", ""), data.get("personToMeet", ""),
             data.get("purpose", ""), data.get("notes", ""), stamp, selfie_filename))
        if CLOUD:
            conn.execute("UPDATE visitors SET selfie_bytes=? WHERE visitor_id=?", (selfie_bytes, visitor_id))
        conn.commit()
    audit_log("VISITOR_REGISTERED", "visitor", f"Visitor ID: {visitor_id}")
    return visitor_id, stamp



@app.get("/")
def home():
    token = session.setdefault("public_csrf", secrets.token_urlsafe(32))
    return render_template("index.html", public_csrf=token)



@app.get("/health")
def health():
    return {"status": "ok", "project": "Visitor Helper Desk", "phase": 4}



@app.post("/api/register")
def register_visitor():
    if request.is_json:
        data = request.get_json(silent=True)
        selfie = None
    else:
        data = request.form.to_dict()
        selfie = request.files.get("selfie")
    if not isinstance(data, dict):
        return jsonify(message="Invalid request. Please submit the form again."), 400
    if not require_public_csrf():
        return jsonify(message="Invalid request token. Refresh the page and try again."), 400
    try:
        validated = validate_registration(data)
    except ValueError as exc:
        return jsonify(message=str(exc)), 400

    if selfie is None or not selfie.filename:
        return jsonify(message="Please upload a selfie before registering."), 400
    allowed, retry_after = rate_limit_status(_registration_events, client_ip(), time.time(), REGISTRATION_LIMIT, REGISTRATION_WINDOW_SECONDS)
    if not allowed:
        audit_log("VISITOR_REGISTRATION_RATE_LIMIT", "security", "Registration rate limit reached")
        return jsonify(message="Too many registration attempts. Try again later."), 429, {"Retry-After": str(retry_after)}
    selfie_filename = secrets.token_urlsafe(24) + ".jpg"
    selfie_bytes = None
    try:
        raw = selfie.stream.read(MAX_SELFIE_BYTES + 1)
        if not raw or len(raw) > MAX_SELFIE_BYTES:
            return jsonify(message="Selfie must be a non-empty image of 3 MB or smaller."), 400
        with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG", "WEBP"]) as img:
            if img.width * img.height > Image.MAX_IMAGE_PIXELS or getattr(img, "n_frames", 1) != 1:
                raise ValueError("Invalid image dimensions or animation")
            img.load()
            clean = ImageOps.exif_transpose(img).convert("RGB")
            clean.thumbnail((1600, 1600))
            if CLOUD:
                buffer = io.BytesIO()
                clean.save(buffer, format="JPEG", quality=85)
                selfie_bytes = buffer.getvalue()
            else:
                os.makedirs(SELFIE_DIR, mode=0o700, exist_ok=True)
                with open(os.path.join(SELFIE_DIR, selfie_filename), "xb") as dest:
                    clean.save(dest, format="JPEG", quality=88)
    except (ValueError, OSError, Image.DecompressionBombError):
        try:
            if not CLOUD: os.remove(os.path.join(SELFIE_DIR, selfie_filename))
        except OSError: pass
        return jsonify(message="Invalid or unsupported selfie image."), 400
    try:
        visitor_id, registered_at = create_visitor(validated, selfie_filename, selfie_bytes)
    except DB_ERRORS:
        if selfie_filename and not CLOUD:
            try: os.remove(os.path.join(SELFIE_DIR, selfie_filename))
            except OSError: pass
        app.logger.exception("Failed to save visitor registration")
        return jsonify(message="Registration could not be saved. Please contact reception."), 500
    return jsonify(message="Visitor registration saved successfully.", visitorId=visitor_id,
                   registeredAt=registered_at), 201


@app.get("/admin/visitor/<int:visitor_pk>/selfie")
def admin_visitor_selfie(visitor_pk):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    with closing(get_db()) as conn:
        row = conn.execute("SELECT selfie_filename, selfie_bytes FROM visitors WHERE id = ?" if CLOUD else "SELECT selfie_filename FROM visitors WHERE id = ?", (visitor_pk,)).fetchone()
    if CLOUD:
        if not row or not row["selfie_bytes"]: return "Selfie not found", 404
        return send_file(io.BytesIO(bytes(row["selfie_bytes"])), mimetype="image/jpeg", download_name="visitor-selfie.jpg")
    if not row or not row["selfie_filename"]:
        return "Selfie not found", 404
    filename = os.path.basename(row["selfie_filename"])
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}\.(jpg|png|webp)", filename) or not os.path.isfile(os.path.join(SELFIE_DIR, filename)):
        return "Selfie not found", 404
    return send_file(os.path.join(SELFIE_DIR, filename), as_attachment=False,
                     download_name="visitor-selfie" + os.path.splitext(filename)[1])


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = ""
    login_csrf = session.setdefault("login_csrf", secrets.token_urlsafe(32))
    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not secrets.compare_digest(submitted_token.encode(), login_csrf.encode()):
            return "Invalid login request token. Refresh and try again.", 400
        ip = client_ip()
        allowed, retry_after = rate_limit_status(_login_failures, ip, time.time(), LOGIN_MAX_ATTEMPTS, LOGIN_WINDOW_SECONDS)
        if not allowed:
            error = "Too many login attempts. Please wait 5 minutes and try again."
        else:
            password = request.form.get("password", "")
            configured_password = os.environ.get("VHD_ADMIN_PASSWORD")
            if configured_password and not configured_password.lower().startswith(("change", "replace", "example")) and secrets.compare_digest(password.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")):
                session.clear()  # prevent session fixation; issue a fresh authenticated session
                session["admin_authenticated"] = True
                session["csrf_token"] = secrets.token_urlsafe(32)
                session.permanent = True
                with _rate_lock:
                    _login_failures.pop(ip, None)
                if CLOUD:
                    with closing(get_db()) as conn:
                        conn.execute("DELETE FROM rate_events WHERE scope=? AND ip=?", ("login", ip))
                        conn.commit()
                audit_log("ADMIN_LOGIN_SUCCESS", "admin", "Admin session authenticated", actor="admin")
                return redirect(url_for("admin_dashboard"))
            audit_log("ADMIN_LOGIN_FAILURE", "security", "Failed admin authentication", actor="unknown")
            error = "Incorrect admin password or admin password is not configured."
        login_csrf = session.setdefault("login_csrf", secrets.token_urlsafe(32))
    return render_template("admin_login.html", error=error, csrf_token=login_csrf)



@app.post("/admin/logout")
def admin_logout():
    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("csrf_token", "")
    if not expected_token or not secrets.compare_digest(submitted_token.encode(), expected_token.encode()):
        return "Invalid request token. Please refresh and try again.", 400
    audit_log("ADMIN_LOGOUT", "admin", "Admin logged out", actor="admin")
    session.clear()
    return redirect(url_for("admin_login"))



@app.get("/admin")
def admin_dashboard():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))

    csrf_token = session.setdefault("csrf_token", secrets.token_urlsafe(32))
    search = request.args.get("q", "").strip()[:100]
    visitor_type = request.args.get("type", "").strip()[:40]
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    for date_value in (date_from, date_to):
        if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            return "Invalid date filter", 400
    if date_from and date_to and date_from > date_to:
        return "Start date must be before or equal to end date", 400
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 20

    conditions, params = [], []
    if search:
        like = f"%{search}%"
        conditions.append("(visitor_id LIKE ? OR full_name LIKE ? OR mobile LIKE ? OR organization LIKE ? OR email LIKE ? OR person_to_meet LIKE ? OR purpose LIKE ?)")
        params.extend([like] * 7)
    if visitor_type:
        conditions.append("visitor_type = ?")
        params.append(visitor_type)
    if date_from:
        conditions.append("date(registered_at) >= date(?)")
        params.append(date_from)
    if date_to:
        conditions.append("date(registered_at) <= date(?)")
        params.append(date_to)
    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""

    today = datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date().isoformat()
    with closing(get_db()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0]
        today_count = conn.execute("SELECT COUNT(*) FROM visitors WHERE date(registered_at)=date(?)", (today,)).fetchone()[0]
        type_count = conn.execute("SELECT COUNT(DISTINCT visitor_type) FROM visitors").fetchone()[0]
        filtered_count = conn.execute("SELECT COUNT(*) FROM visitors" + where_sql, params).fetchone()[0]
        pages = max(1, (filtered_count + per_page - 1) // per_page)
        page = min(page, pages)
        rows = conn.execute("SELECT id, visitor_id, full_name, mobile, visitor_type, organization, email, person_to_meet, purpose, notes, mobile_verified, registered_at, selfie_filename FROM visitors" + where_sql + " ORDER BY id DESC LIMIT ? OFFSET ?",
                          params + [per_page, (page - 1) * per_page]).fetchall()
        visitor_types = conn.execute("SELECT DISTINCT visitor_type FROM visitors ORDER BY visitor_type").fetchall()
    return render_template("admin.html", visitors=rows, count=total, today_count=today_count,
                         type_count=type_count, filtered_count=filtered_count, page=page,
                         pages=pages, search=search, selected_type=visitor_type,
                         date_from=date_from, date_to=date_to, visitor_types=visitor_types, csrf_token=csrf_token, backup_retention_days=BACKUP_RETENTION_DAYS, database_integrity=database_integrity_status(), cloud=CLOUD)


@app.get("/admin/visitor/<int:visitor_pk>")
def admin_visitor_detail(visitor_pk):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    with closing(get_db()) as conn:
        visitor = conn.execute("SELECT id, visitor_id, full_name, mobile, visitor_type, organization, email, person_to_meet, purpose, notes, mobile_verified, registered_at, selfie_filename FROM visitors WHERE id = ?", (visitor_pk,)).fetchone()
    if not visitor:
        audit_log("VISITOR_DETAIL_NOT_FOUND", "security", f"Record ID: {visitor_pk}")
        return "Visitor record not found", 404
    audit_log("VISITOR_DETAIL_VIEWED", "visitor", f"Visitor ID: {visitor['visitor_id']}")
    return render_template("admin_detail.html", visitor=visitor)



@app.post("/admin/visitor/<int:visitor_pk>/delete")
def admin_delete_visitor(visitor_pk):
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    submitted_token = request.form.get("csrf_token", "")
    if not session.get("csrf_token") or not secrets.compare_digest(submitted_token.encode(), session["csrf_token"].encode()):
        return "Invalid request token. Please refresh the dashboard and try again.", 400
    with closing(get_db()) as conn:
        existing = conn.execute("SELECT visitor_id FROM visitors WHERE id = ?", (visitor_pk,)).fetchone()
        conn.execute("DELETE FROM visitors WHERE id = ?", (visitor_pk,))
        conn.commit()
    audit_log("VISITOR_RECORD_DELETED", "visitor", f"Visitor ID: {existing['visitor_id']}" if existing else "Visitor ID: not found")
    return redirect(url_for("admin_dashboard"))



def csv_safe(value):
    if isinstance(value, str) and (value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n"))):
        return "'" + value
    return value

@app.get("/admin/export.csv")
def export_csv():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    search = request.args.get("q", "").strip()[:100]
    visitor_type = request.args.get("type", "").strip()[:40]
    date_from = request.args.get("from", "").strip()
    date_to = request.args.get("to", "").strip()
    for date_value in (date_from, date_to):
        if date_value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_value):
            return "Invalid date filter", 400
    if date_from and date_to and date_from > date_to:
        return "Start date must be before or equal to end date", 400
    conditions, params = [], []
    if search:
        like = f"%{search}%"
        conditions.append("(visitor_id LIKE ? OR full_name LIKE ? OR mobile LIKE ? OR organization LIKE ? OR email LIKE ? OR person_to_meet LIKE ? OR purpose LIKE ?)")
        params.extend([like] * 7)
    if visitor_type:
        conditions.append("visitor_type = ?")
        params.append(visitor_type)
    if date_from:
        conditions.append("date(registered_at) >= date(?)")
        params.append(date_from)
    if date_to:
        conditions.append("date(registered_at) <= date(?)")
        params.append(date_to)
    where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
    with closing(get_db()) as conn:
        rows = conn.execute("SELECT visitor_id, full_name, mobile, visitor_type, organization, email, person_to_meet, purpose, notes, mobile_verified, registered_at FROM visitors" + where_sql + " ORDER BY id DESC", params).fetchall()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Visitor ID", "Full Name", "Mobile", "Visitor Type", "College / Designation / Occupation", "Email", "Person/Department to Meet", "Purpose", "Additional Information", "Mobile Verified", "Registered At"])
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    audit_log("VISITOR_CSV_EXPORTED", "admin", f"Exported {len(rows)} record(s) with active filters")
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=visitor_records.csv"})



@app.get("/admin/security-logs")
def admin_security_logs():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "").strip()[:40]
    date_from = request.args.get("from", "").strip(); date_to = request.args.get("to", "").strip()
    for val in (date_from, date_to):
        if val and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val): return "Invalid date filter", 400
    if date_from and date_to and date_from > date_to: return "Start date must be before or equal to end date", 400
    clauses=[]; params=[]
    if q:
        like=f"%{q}%"; clauses.append("(event LIKE ? OR details LIKE ? OR actor LIKE ? OR ip_address LIKE ?)"); params.extend([like]*4)
    if category: clauses.append("category = ?"); params.append(category)
    if date_from: clauses.append("date(created_at) >= date(?)"); params.append(date_from)
    if date_to: clauses.append("date(created_at) <= date(?)"); params.append(date_to)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    page=max(1, request.args.get("page",1,type=int)); per_page=50
    with closing(get_db()) as conn:
        count=conn.execute("SELECT COUNT(*) FROM audit_logs"+where,params).fetchone()[0]
        pages=max(1,(count+per_page-1)//per_page); page=min(page,pages)
        logs=conn.execute("SELECT * FROM audit_logs"+where+" ORDER BY id DESC LIMIT ? OFFSET ?",params+[per_page,(page-1)*per_page]).fetchall()
        categories=conn.execute("SELECT DISTINCT category FROM audit_logs ORDER BY category").fetchall()
    return render_template("security_logs.html", logs=logs, count=count, pages=pages, page=page, q=q, category=category, date_from=date_from, date_to=date_to, categories=categories)

@app.get("/admin/security-logs/export.csv")
def admin_security_logs_export():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    q=request.args.get("q","").strip()[:100]; category=request.args.get("category","").strip()[:40]
    date_from=request.args.get("from","").strip(); date_to=request.args.get("to","").strip()
    for val in (date_from, date_to):
        if val and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val): return "Invalid date filter", 400
    if date_from and date_to and date_from > date_to: return "Start date must be before or equal to end date", 400
    clauses=[]; params=[]
    if q:
        like=f"%{q}%"; clauses.append("(event LIKE ? OR details LIKE ? OR actor LIKE ? OR ip_address LIKE ?)"); params.extend([like]*4)
    if category: clauses.append("category = ?"); params.append(category)
    if date_from: clauses.append("date(created_at) >= date(?)"); params.append(date_from)
    if date_to: clauses.append("date(created_at) <= date(?)"); params.append(date_to)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    with closing(get_db()) as conn: rows=conn.execute("SELECT created_at,event,category,details,actor,ip_address FROM audit_logs"+where+" ORDER BY id DESC",params).fetchall()
    out=io.StringIO(); writer=csv.writer(out); writer.writerow(["Timestamp","Event","Category","Details","Actor","IP Address"])
    writer.writerows([[csv_safe(v) for v in row] for row in rows])
    audit_log("AUDIT_LOGS_EXPORTED", "admin", f"Exported {len(rows)} audit event(s)")
    return Response(out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=security_audit_logs.csv"})

@app.get("/admin/backup")
def admin_backup_download():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    if CLOUD: return "Use your PostgreSQL provider backup or pg_dump.", 403
    try:
        path = create_database_backup(prefix="visitor_records_manual")
        if not path:
            return "Database file not found", 404
        audit_log("DATABASE_BACKUP_DOWNLOADED", "admin", "Manual database backup downloaded")
        return send_file(path, as_attachment=True, download_name=os.path.basename(path), mimetype="application/octet-stream")
    except Exception:
        app.logger.exception("Manual database backup failed")
        return "Backup creation failed. Check server logs.", 500


@app.get("/admin/database-status")
def admin_database_status():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    return jsonify(integrity=database_integrity_status(), retention_days=BACKUP_RETENTION_DAYS)



@app.post("/admin/restore")
def admin_restore_database():
    if not session.get("admin_authenticated"):
        return redirect(url_for("admin_login"))
    token = request.form.get("csrf_token", "")
    expected = session.get("csrf_token", "")
    if not expected or not secrets.compare_digest(token.encode(), expected.encode()):
        return "Invalid request token. Refresh the dashboard and try again.", 400
    if IS_PRODUCTION or CLOUD:
        return "Online restore disabled in production. Use offline maintenance restore.", 403
    if request.form.get("confirm_restore") != "RESTORE":
        return "Restore confirmation is required. Type RESTORE to proceed.", 400
    uploaded = request.files.get("backup_file")
    if not uploaded or not uploaded.filename:
        return "Select a backup file first.", 400
    temp_dir = tempfile.mkdtemp(prefix="vhd_restore_", dir=DATA_DIR)
    candidate = os.path.join(temp_dir, "uploaded_restore.sqlite3")
    try:
        uploaded.save(candidate)
        if os.path.getsize(candidate) > MAX_RESTORE_UPLOAD_BYTES:
            return "Backup file is too large (maximum 50 MB).", 413
        validate_restore_database(candidate)
        # Protect current data first, then atomically swap the validated database.
        pre_restore = create_database_backup(prefix="visitor_records_pre_restore")
        if not pre_restore:
            return "Current database backup could not be created; restore cancelled.", 500
        os.replace(candidate, DB_PATH)
        init_db()
        audit_log("DATABASE_RESTORED", "admin", f"Restore completed; pre-restore backup: {os.path.basename(pre_restore)}")
        app.logger.warning("Database restored by authenticated admin. Pre-restore backup: %s", os.path.basename(pre_restore))
        prune_old_backups()
        return redirect(url_for("admin_dashboard"))
    except ValueError as exc:
        return str(exc), 400
    except Exception:
        app.logger.exception("Database restore failed")
        return "Restore failed. The current database was not intentionally overwritten unless validation and pre-backup completed.", 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)



init_db()
# Phase 6.4: startup snapshot and retention cleanup (best-effort; app still starts if backup fails).
try:
    if not CLOUD:
        create_database_backup(prefix="visitor_records_auto")
        prune_old_backups()
    with closing(get_db()) as conn:
        if CLOUD:
            conn.execute("DELETE FROM audit_logs WHERE created_at < ?", ((datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))-timedelta(days=AUDIT_RETENTION_DAYS)).isoformat(timespec="seconds"),))
            conn.execute("DELETE FROM rate_events WHERE stamp < ?", (time.time()-86400,))
        else:
            conn.execute("DELETE FROM audit_logs WHERE datetime(created_at) < datetime('now', ?)", (f"-{AUDIT_RETENTION_DAYS} days",))
        conn.commit()
except Exception:
    app.logger.exception("Startup database backup/retention task failed")

if __name__ == "__main__":
    if IS_PRODUCTION:
        raise RuntimeError("Use waitress-serve for production, behind HTTPS.")
    app.run(host="0.0.0.0", port=int(os.environ.get("VHD_PORT", "5000")), debug=False)
