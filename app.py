import os
import io
import shutil
import ipaddress
import json
import hashlib
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.middleware.proxy_fix import ProxyFix
from docxtpl import DocxTemplate
from exam_options import EXAM_CATEGORIES, EXAM_PURPOSES
from zeep import Client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import pyzipper
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import hl7 
import redis

load_dotenv()

app = Flask(__name__)

from flask_session import Session

def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value

app.config['SESSION_TYPE'] = 'redis'
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_REDIS'] = redis.from_url("redis://kai-redis:6379/0") # Χρησιμοποιούμε το όνομα του container [cite: 4]
Session(app)

# --- ΠΡΟΣΤΑΣΙΑ ΑΠΟ BRUTE FORCE (RATE LIMITING) ---
# Χρησιμοποιεί την πραγματική IP του χρήστη (ακόμα και πίσω από τον Proxy)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"], # Γενικά όρια για όλο το site
    storage_uri="redis://kai-redis:6379/0"
)

# --- 1. ENTERPRISE REVERSE PROXY FIX ---
# Λέμε στην Flask να διαβάζει τα σωστά IP και Πρωτόκολλα (HTTPS) από το Docker/Nginx
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

# Εξαναγκάζουμε την Flask να δημιουργεί ΠΑΝΤΑ HTTPS links στα redirects
app.config['PREFERRED_URL_SCHEME'] = 'https'

def bool_env(name, default="0"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

def csv_env(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]

TRUST_SSO_HEADERS = bool_env("TRUST_SSO_HEADERS", "0")
SSO_TRUSTED_PROXY_CIDRS = csv_env("SSO_TRUSTED_PROXY_CIDRS", "127.0.0.1/32,172.16.0.0/12")
SSO_KAI_USER_GROUP = os.getenv("SSO_KAI_USER_GROUP", "/apps/kai/users")
SSO_KAI_ADMIN_GROUP = os.getenv("SSO_KAI_ADMIN_GROUP", "/apps/kai/admins")
SSO_GLOBAL_ADMIN_GROUP = os.getenv("SSO_GLOBAL_ADMIN_GROUP", "/apps/global/admins")
CENTRAL_AUTH_REALM = os.getenv("CENTRAL_AUTH_REALM", "intranet")
CENTRAL_AUTH_ADMIN_URL = os.getenv("CENTRAL_AUTH_ADMIN_URL", "https://auth.251gh.local/admin/")
CENTRAL_AUTH_USERS_URL = os.getenv(
    "CENTRAL_AUTH_USERS_URL",
    f"https://auth.251gh.local/admin/master/console/#/{CENTRAL_AUTH_REALM}/users",
)
CENTRAL_AUTH_GROUPS_URL = os.getenv(
    "CENTRAL_AUTH_GROUPS_URL",
    f"https://auth.251gh.local/admin/master/console/#/{CENTRAL_AUTH_REALM}/groups",
)
ALLOW_LOCAL_USER_ADMIN_FROM_SSO = bool_env("ALLOW_LOCAL_USER_ADMIN_FROM_SSO", "0")
APPOINTMENTS_FEATURE_ENABLED = bool_env("APPOINTMENTS_FEATURE_ENABLED", "0")
APPOINTMENTS_DISABLED_MESSAGE = "Τα αντικείμενα του παρόντος θα ενεργοποιηθούν σε μελλοντική αναβάθμιση."

# Middleware Ασφαλείας: Ακόμα κι αν ο Proxy κάνει λάθος, εμείς επιβάλλουμε το HTTPS εσωτερικά
class ForceHTTPSMiddleware:
    def __init__(self, app):
        self.app = app
    def __call__(self, environ, start_response):
        environ['wsgi.url_scheme'] = 'https'
        return self.app(environ, start_response)

app.wsgi_app = ForceHTTPSMiddleware(app.wsgi_app)

# Παίρνει το σταθερό κλειδί από το .env
app.secret_key = required_env('FLASK_SECRET_KEY')

# --- 2. ISO 27799: ΣΥΣΤΗΜΑ ΑΣΦΑΛΕΙΑΣ ΣΥΝΕΔΡΙΑΣ & COOKIES ---
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=6)
app.config['SESSION_COOKIE_NAME'] = 'kai_secure_medical_session' # Μοναδικό όνομα για αποφυγή συγκρούσεων
app.config['SESSION_COOKIE_SECURE'] = True    # ΑΥΣΤΗΡΟ HTTPS ONLY
app.config['SESSION_COOKIE_HTTPONLY'] = True  # Προστασία από XSS
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax' # Προστασία από CSRF

csrf = CSRFProtect(app)

# --- 3. ΣΥΣΤΗΜΑ LOGIN (ΡΥΘΜΙΣΜΕΝΟ ΓΙΑ DOCKER) ---
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Παρακαλώ συνδεθείτε (ή η συνεδρία σας έληξε λόγω αδράνειας)."
login_manager.login_message_category = "warning"
# Το 'basic' προσφέρει ασφάλεια αλλά αποτρέπει τις τυχαίες αποσυνδέσεις λόγω αλλαγής IP του Docker
login_manager.session_protection = 'basic' 

# --- 4. ΔΙΑΧΕΙΡΙΣΗ ΣΥΝΕΔΡΙΑΣ & ΑΔΡΑΝΕΙΑΣ ---
# --- 4. ΔΙΑΧΕΙΡΙΣΗ ΣΥΝΕΔΡΙΑΣ & ΑΔΡΑΝΕΙΑΣ ---
@app.before_request
def manage_session():
    session.permanent = True 
    
    # Χειροκίνητος έλεγχος αδράνειας
    if 'last_active' in session:
        try:
            last_active = datetime.fromisoformat(session['last_active'])
            
            # 1. Αν πέρασαν 6 ώρες, τον πετάμε έξω
            if datetime.now() - last_active > timedelta(hours=6):
                session.clear() 
                flash("Η συνεδρία σας έληξε λόγω αδράνειας. Παρακαλώ συνδεθείτε ξανά.", "warning")
                return redirect(url_for('login'))
                
            # 2. ΔΙΟΡΘΩΣΗ ΓΙΑ ΤΑ ΠΟΛΛΑ ΚΛΙΚ (Race Condition Fix):
            # Αν το τελευταίο κλικ έγινε πριν από λιγότερο από 5 δευτερόλεπτα,
            # ΔΕΝ στέλνουμε νέο cookie στον browser. Αυτό εμποδίζει το "μπέρδεμα"
            # των workers που σε πετούσε στο Login.
            if datetime.now() - last_active < timedelta(seconds=5):
                return
                
        except ValueError:
            pass

    if request.endpoint not in {"static", "logout"}:
        sync_sso_session()
            
    # Ανανέωση χρόνου και αποστολή cookie (μόνο αν πέρασε το 5δευτερόλεπτο)
    session['last_active'] = datetime.now().isoformat()
    session.modified = True

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    flash("Η συνεδρία σας έληξε. Παρακαλώ συνδεθείτε ξανά.", "warning")
    return redirect(url_for('login'))

@app.after_request
def add_security_headers(response):
    """ Απαγορεύει την αποθήκευση (Cache) των HTML σελίδων για μέγιστη ιατρική εχεμύθεια. """
    if 'text/html' in response.headers.get('Content-Type', ''):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# --- ΡΥΘΜΙΣΕΙΣ ΒΑΣΗΣ & ΦΑΚΕΛΩΝ ---
BASE_DIR = os.path.abspath(os.path.dirname(__name__))
db_url = os.getenv("DATABASE_URL")
app.config['SQLALCHEMY_DATABASE_URI'] = db_url if db_url else 'sqlite:///' + os.path.join(BASE_DIR, 'kai_exams.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(BASE_DIR, 'static', 'scans')
app.config['TEMPLATE_FOLDER'] = os.path.join(BASE_DIR, 'static', 'templates')
BACKUP_FOLDER = os.path.join(BASE_DIR, 'backups')
BACKUP_RETENTION_COUNT = int(os.getenv("BACKUP_RETENTION_COUNT", "30"))
BACKUP_INCLUDE_DATABASE = bool_env("BACKUP_INCLUDE_DATABASE", "1")

# --- ΡΥΘΜΙΣΕΙΣ ΒΑΣΗΣ & ΦΑΚΕΛΩΝ ---
# ... (υπάρχον κώδικας) ...
for folder in [app.config['UPLOAD_FOLDER'], BACKUP_FOLDER]:
    os.makedirs(folder, exist_ok=True)

db = SQLAlchemy(app)

# --- ΝΕΟ: ΑΣΦΑΛΕΙΑ ΑΝΕΒΑΣΜΕΝΩΝ ΑΡΧΕΙΩΝ (INTERNET SECURITY) ---
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}
DRAFT_TEMPLATE_FILE = 'HER_KAI.docx'
OPTION_TYPE_PURPOSE = 'purpose'
OPTION_TYPE_CATEGORY = 'category'
OPTION_TYPE_LABELS = {
    OPTION_TYPE_PURPOSE: 'Σκοπός εξέτασης',
    OPTION_TYPE_CATEGORY: 'Κατηγορία εξέτασης',
}
DEFAULT_OPTION_SETS = {
    OPTION_TYPE_PURPOSE: EXAM_PURPOSES,
    OPTION_TYPE_CATEGORY: EXAM_CATEGORIES,
}

def allowed_file(filename):
    """ Ελέγχει αν το αρχείο έχει κατάληξη και αν αυτή ανήκει στις επιτρεπόμενες """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def is_admin_user():
    return current_user.is_authenticated and (
        getattr(current_user, "sso_admin", False)
        or current_user.username == os.getenv("ADMIN_USERNAME", "admin")
    )

def can_change_password():
    return current_user.is_authenticated and getattr(current_user, "auth_method", "local") != "sso"

def format_display_date(value):
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime('%d/%m/%Y')

    text_value = str(value).strip()
    if not text_value:
        return ""

    for date_format in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%m/%d/%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text_value, date_format).strftime('%d/%m/%Y')
        except ValueError:
            continue
    return text_value

@app.context_processor
def inject_auth_helpers():
    return {
        "is_admin_user": is_admin_user,
        "can_change_password": can_change_password,
        "format_display_date": format_display_date,
        "appointments_feature_enabled": APPOINTMENTS_FEATURE_ENABLED,
        "appointments_disabled_message": APPOINTMENTS_DISABLED_MESSAGE,
    }

@app.template_filter("filesize")
def filesize_filter(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024

@app.template_filter("dmy")
def dmy_filter(value):
    return format_display_date(value)

def local_user_admin_allowed():
    if not is_admin_user():
        return False
    if getattr(current_user, "auth_method", "local") == "sso":
        return ALLOW_LOCAL_USER_ADMIN_FROM_SSO
    return True

def central_auth_context():
    kai_groups = [
        {
            "name": SSO_KAI_USER_GROUP,
            "role": "Απλός χρήστης KAI",
            "description": "Πρόσβαση στην εφαρμογή KAI μέσω SSO.",
        },
        {
            "name": SSO_KAI_ADMIN_GROUP,
            "role": "Διαχειριστής KAI",
            "description": "Πρόσβαση KAI και δικαιώματα διαχείρισης μέσα στην εφαρμογή.",
        },
        {
            "name": SSO_GLOBAL_ADMIN_GROUP,
            "role": "Συνολικός διαχειριστής",
            "description": "Break-glass/admin πρόσβαση σε όλες τις εφαρμογές SSO.",
        },
    ]
    return {
        "realm": CENTRAL_AUTH_REALM,
        "admin_url": CENTRAL_AUTH_ADMIN_URL,
        "users_url": CENTRAL_AUTH_USERS_URL,
        "groups_url": CENTRAL_AUTH_GROUPS_URL,
        "groups": kai_groups,
        "current_groups": getattr(current_user, "sso_groups", []),
        "auth_method": getattr(current_user, "auth_method", "local"),
    }

def parse_sso_groups(value):
    return [group.strip() for group in (value or "").split(",") if group.strip()]

def normalize_remote_address(value):
    address = (value or "").strip()
    if address.startswith("::ffff:"):
        return address[7:]
    return address

def request_peer_address():
    proxy_fix_original = request.environ.get("werkzeug.proxy_fix.orig") or {}
    return normalize_remote_address(proxy_fix_original.get("REMOTE_ADDR") or request.remote_addr or "")

def is_trusted_sso_proxy():
    remote_address = request_peer_address()
    if not remote_address:
        return False
    if remote_address == "::1":
        return True
    try:
        peer = ipaddress.ip_address(remote_address)
    except ValueError:
        return False
    for cidr in SSO_TRUSTED_PROXY_CIDRS:
        try:
            if peer in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False

def current_sso_user():
    if not TRUST_SSO_HEADERS or not is_trusted_sso_proxy():
        return None
    groups = parse_sso_groups(request.headers.get("X-SSO-Groups", ""))
    sso_admin = SSO_KAI_ADMIN_GROUP in groups or SSO_GLOBAL_ADMIN_GROUP in groups
    sso_user = SSO_KAI_USER_GROUP in groups
    if not (sso_user or sso_admin):
        return None
    username = (
        request.headers.get("X-SSO-Preferred-Username")
        or request.headers.get("X-SSO-User")
        or request.headers.get("X-SSO-Email")
        or ""
    ).strip()
    if not username:
        return None
    return SSOUser(username, sso_admin=sso_admin, groups=groups)

def sync_sso_session():
    sso_user = current_sso_user()
    if not sso_user:
        if (
            TRUST_SSO_HEADERS
            and current_user.is_authenticated
            and getattr(current_user, "auth_method", "local") == "sso"
        ):
            session.pop("sso_user", None)
            logout_user()
        return None
    existing = session.get("sso_user") or {}
    if (
        current_user.is_authenticated
        and getattr(current_user, "auth_method", "local") == "sso"
        and existing.get("id") == sso_user.id
        and existing.get("sso_admin") == sso_user.sso_admin
        and existing.get("sso_groups") == sso_user.sso_groups
    ):
        return current_user

    session["sso_user"] = {
        "id": sso_user.id,
        "username": sso_user.username,
        "sso_admin": sso_user.sso_admin,
        "sso_groups": sso_user.sso_groups,
    }
    login_user(sso_user)
    log_action("LOGIN", "System", f"SSO login: {sso_user.username}")
    return sso_user

def _get_active_exam_options(option_type, fallback):
    try:
        options = ExamOption.query.filter_by(option_type=option_type, is_active=True).order_by(
            ExamOption.sort_order.asc(),
            ExamOption.label.asc()
        ).all()
        if options:
            return [option.label for option in options]

        if ExamOption.query.filter_by(option_type=option_type).count() == 0:
            return list(fallback)
        return []
    except Exception:
        return list(fallback)

def get_exam_purposes():
    return _get_active_exam_options(OPTION_TYPE_PURPOSE, EXAM_PURPOSES)

def get_exam_categories():
    return _get_active_exam_options(OPTION_TYPE_CATEGORY, EXAM_CATEGORIES)

def _parse_sort_order(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _next_exam_option_sort_order(option_type):
    max_sort_order = db.session.query(db.func.max(ExamOption.sort_order)).filter_by(option_type=option_type).scalar()
    return (max_sort_order or 0) + 10

def _seed_exam_options():
    created = False
    for option_type, labels in DEFAULT_OPTION_SETS.items():
        existing_labels = {
            option.label
            for option in ExamOption.query.filter_by(option_type=option_type).all()
        }
        for index, label in enumerate(labels, start=1):
            if label in existing_labels:
                continue
            db.session.add(ExamOption(
                option_type=option_type,
                label=label,
                sort_order=index * 10,
                is_active=True
            ))
            created = True

    if created:
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True)
    password_hash = db.Column(db.String(200))

class SSOUser(UserMixin):
    def __init__(self, username, *, sso_admin=False, groups=None):
        self.id = f"sso:{username}"
        self.username = username
        self.auth_method = "sso"
        self.sso_admin = bool(sso_admin)
        self.sso_groups = groups or []

@login_manager.user_loader
def load_user(user_id):
    if str(user_id).startswith("sso:"):
        sso_session = session.get("sso_user") or {}
        if sso_session.get("id") != user_id:
            return None
        return SSOUser(
            sso_session.get("username", ""),
            sso_admin=sso_session.get("sso_admin", False),
            groups=sso_session.get("sso_groups", []),
        )
    return User.query.get(int(user_id))

# --- ΜΟΝΤΕΛΑ ΒΑΣΗΣ ---
class Exam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asma = db.Column(db.String(20), nullable=False)
    eponymo = db.Column(db.String(100))
    onoma = db.Column(db.String(100))
    patronymo = db.Column(db.String(100))
    vathmos = db.Column(db.String(50))
    monada = db.Column(db.String(50))
    amka = db.Column(db.String(20))
    hm_gen = db.Column(db.String(20))
    kinito = db.Column(db.String(30))
    katigoria = db.Column(db.String(50))
    skopos = db.Column(db.String(50))  
    exam_date = db.Column(db.DateTime, default=datetime.utcnow)
    porisma = db.Column(db.String(50), default="ΕΚΚΡΕΜΕΙ")
    valid_until = db.Column(db.String(50))
    scan_filepath = db.Column(db.String(255))
    created_by = db.Column(db.String(50))

class ExamOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    option_type = db.Column(db.String(20), nullable=False, index=True)
    label = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('option_type', 'label', name='uq_exam_option_type_label'),
    )

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    username = db.Column(db.String(50))
    action = db.Column(db.String(50))
    target = db.Column(db.String(100))
    details = db.Column(db.String(255))

class BackupRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    filename = db.Column(db.String(255), nullable=False, unique=True)
    path = db.Column(db.String(500), nullable=False)
    backup_type = db.Column(db.String(30), default="manual", index=True)
    status = db.Column(db.String(30), default="RUNNING", index=True)
    created_by = db.Column(db.String(100))
    auth_method = db.Column(db.String(30))
    sso_groups = db.Column(db.Text)
    file_count = db.Column(db.Integer, default=0)
    size_bytes = db.Column(db.Integer, default=0)
    sha256 = db.Column(db.String(64))
    manifest_json = db.Column(db.Text)
    verified_at = db.Column(db.DateTime)
    verify_message = db.Column(db.String(255))

def log_action(action, target, details):
    try:
        user = current_user.username if getattr(current_user, "is_authenticated", False) else "System"
    except (RuntimeError, AttributeError):
        user = "System"
    new_log = AuditLog(username=user, action=action, target=target, details=details)
    db.session.add(new_log)
    db.session.commit()

def create_all_with_retry(retries=3, delay_seconds=0.5):
    for attempt in range(retries):
        try:
            db.create_all()
            return
        except IntegrityError:
            db.session.rollback()
            if attempt == retries - 1:
                raise
            time.sleep(delay_seconds)

def ensure_schema_upgrades():
    inspector = inspect(db.engine)
    exam_columns = {column["name"] for column in inspector.get_columns(Exam.__tablename__)}
    if "kinito" not in exam_columns:
        preparer = db.engine.dialect.identifier_preparer
        table_name = preparer.quote(Exam.__tablename__)
        column_name = preparer.quote("kinito")
        with db.engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} VARCHAR(30)"))

with app.app_context():
    create_all_with_retry()
    ensure_schema_upgrades()
    _seed_exam_options()
    admin_user = os.getenv("ADMIN_USERNAME", "admin")
    if not User.query.filter_by(username=admin_user).first():
        admin_pass = required_env("ADMIN_PASSWORD")
        admin = User(username=admin_user, password_hash=generate_password_hash(admin_pass))
        db.session.add(admin)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if not User.query.filter_by(username=admin_user).first():
                raise

def current_actor_context():
    try:
        if current_user.is_authenticated:
            return {
                "username": current_user.username,
                "auth_method": getattr(current_user, "auth_method", "local"),
                "sso_groups": list(getattr(current_user, "sso_groups", []) or []),
            }
    except RuntimeError:
        pass
    return {"username": "System", "auth_method": "system", "sso_groups": []}

def backup_password():
    return required_env("BACKUP_PASSWORD").encode("utf-8")

def safe_backup_path(filename):
    backup_root = os.path.abspath(BACKUP_FOLDER)
    path = os.path.abspath(os.path.join(backup_root, os.path.basename(filename)))
    if os.path.commonpath([backup_root, path]) != backup_root:
        raise ValueError("Invalid backup path")
    return path

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def git_revision():
    version = os.getenv("KAI_APP_VERSION") or os.getenv("APP_VERSION")
    if version:
        return version
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"

def iter_directory_files(base_path, archive_prefix, *, exclude_dirs=None):
    exclude_dirs = set(exclude_dirs or [])
    root_path = os.path.abspath(base_path)
    if not os.path.isdir(root_path):
        return
    for root, dirs, files in os.walk(root_path):
        dirs[:] = [item for item in dirs if item not in exclude_dirs and item != "__pycache__"]
        for filename in files:
            file_path = os.path.join(root, filename)
            if not os.path.isfile(file_path):
                continue
            rel = os.path.relpath(file_path, root_path).replace("\\", "/")
            yield file_path, f"{archive_prefix}/{rel}"

def sqlite_database_path():
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        return None
    path = uri.replace("sqlite:///", "", 1)
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return path

def write_database_dump(temp_dir):
    if not BACKUP_INCLUDE_DATABASE:
        note_path = os.path.join(temp_dir, "database_backup_disabled.txt")
        with open(note_path, "w", encoding="utf-8") as handle:
            handle.write("Database backup was disabled by BACKUP_INCLUDE_DATABASE=0.\n")
        return note_path, "database/README.txt", "disabled"

    database_url = app.config["SQLALCHEMY_DATABASE_URI"]
    if database_url.startswith("postgresql"):
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError("pg_dump is not installed in the app container")
        dump_path = os.path.join(temp_dir, "kai_database.dump")
        result = subprocess.run(
            [pg_dump, "--dbname", database_url, "--format", "custom", "--file", dump_path],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if result.returncode != 0:
            message = (result.stderr or result.stdout or "pg_dump failed").strip()
            raise RuntimeError(message[:240])
        return dump_path, "database/kai_database.dump", "postgresql-custom"

    sqlite_path = sqlite_database_path()
    if sqlite_path and os.path.exists(sqlite_path):
        dump_path = os.path.join(temp_dir, "kai_exams.sqlite3")
        shutil.copy2(sqlite_path, dump_path)
        return dump_path, "database/kai_exams.sqlite3", "sqlite-copy"

    note_path = os.path.join(temp_dir, "database_not_found.txt")
    with open(note_path, "w", encoding="utf-8") as handle:
        handle.write("No database file or supported DATABASE_URL was found.\n")
    return note_path, "database/README.txt", "missing"

def build_backup_manifest(actor, backup_type, database_mode, entries):
    return {
        "app": "KAI App",
        "format_version": 2,
        "created_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "created_by": actor["username"],
        "auth_method": actor["auth_method"],
        "sso_groups": actor["sso_groups"],
        "backup_type": backup_type,
        "app_revision": git_revision(),
        "database_mode": database_mode,
        "entries": entries,
    }

def enforce_backup_retention():
    if BACKUP_RETENTION_COUNT <= 0:
        return
    records = (
        BackupRecord.query
        .filter(BackupRecord.status.in_(["CREATED", "VERIFIED"]))
        .order_by(BackupRecord.created_at.desc())
        .all()
    )
    for record in records[BACKUP_RETENTION_COUNT:]:
        try:
            if os.path.exists(record.path):
                os.remove(record.path)
            record.status = "PRUNED"
            record.verify_message = f"Pruned by retention policy: keep last {BACKUP_RETENTION_COUNT}"
        except Exception as exc:
            record.status = "PRUNE_FAILED"
            record.verify_message = str(exc)[:255]
    db.session.commit()

def create_backup_record(backup_type="manual", actor=None):
    actor = actor or current_actor_context()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_KAI_{timestamp}_{backup_type}.zip"
    backup_path = safe_backup_path(backup_filename)
    record = BackupRecord(
        filename=backup_filename,
        path=backup_path,
        backup_type=backup_type,
        status="RUNNING",
        created_by=actor["username"],
        auth_method=actor["auth_method"],
        sso_groups=json.dumps(actor["sso_groups"], ensure_ascii=False),
    )
    db.session.add(record)
    db.session.commit()

    entries = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path, database_arcname, database_mode = write_database_dump(temp_dir)
            archive_sources = [(database_path, database_arcname)]
            archive_sources.extend(iter_directory_files(app.config["UPLOAD_FOLDER"], "static/scans"))
            archive_sources.extend(iter_directory_files(
                app.config["TEMPLATE_FOLDER"],
                "static/templates",
                exclude_dirs={"devs_backup"},
            ))
            for _, arcname in archive_sources:
                entries.append(arcname)

            manifest = build_backup_manifest(actor, backup_type, database_mode, entries)
            with pyzipper.AESZipFile(
                backup_path,
                "w",
                compression=pyzipper.ZIP_DEFLATED,
                encryption=pyzipper.WZ_AES,
            ) as archive:
                archive.setpassword(backup_password())
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                for file_path, arcname in archive_sources:
                    archive.write(file_path, arcname)

        record.status = "CREATED"
        record.file_count = len(entries) + 1
        record.size_bytes = os.path.getsize(backup_path)
        record.sha256 = sha256_file(backup_path)
        record.manifest_json = json.dumps(manifest, ensure_ascii=False)
        db.session.commit()
        log_action("BACKUP", f"BackupRecord:{record.id}", f"Created {backup_filename}")
        enforce_backup_retention()
        return record
    except Exception as exc:
        db.session.rollback()
        if os.path.exists(backup_path):
            try:
                os.remove(backup_path)
            except OSError:
                pass
        record = BackupRecord.query.get(record.id)
        if record:
            record.status = "FAILED"
            record.verify_message = str(exc)[:255]
            db.session.commit()
        log_action("BACKUP_ERROR", "System", f"Failed to create backup: {str(exc)[:200]}")
        raise

def verify_backup_record(record):
    if not record or not os.path.exists(record.path):
        raise FileNotFoundError("Backup archive was not found")

    current_sha256 = sha256_file(record.path)
    if record.sha256 and current_sha256 != record.sha256:
        raise RuntimeError("Backup SHA-256 does not match the stored value")

    with pyzipper.AESZipFile(record.path, "r") as archive:
        archive.setpassword(backup_password())
        names = archive.namelist()
        if "manifest.json" not in names:
            raise RuntimeError("Backup manifest is missing")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("app") != "KAI App":
            raise RuntimeError("Backup manifest is not for KAI App")
        for name in names:
            archive.read(name)

    record.status = "VERIFIED"
    record.verified_at = datetime.utcnow()
    record.verify_message = f"OK: {len(names)} encrypted entries verified"
    db.session.commit()
    log_action("BACKUP_VERIFY", f"BackupRecord:{record.id}", record.verify_message)
    return True

# --- ΚΡΥΠΤΟΓΡΑΦΗΜΕΝΟ BACKUP ΣΥΣΤΗΜΑ ---
def backup_system(backup_type="scheduled"):
    with app.app_context():
        try:
            record = create_backup_record(backup_type=backup_type, actor=current_actor_context())
            print(f"[*] Encrypted backup completed: {record.filename}")
            return record
        except Exception as exc:
            print(f"[!] Backup error: {exc}")
            return None

scheduler = BackgroundScheduler()
scheduler.add_job(func=backup_system, trigger="cron", hour=3, minute=0)
scheduler.start()

@app.route('/force_backup')
@login_required
def force_backup():
    if is_admin_user():
        record = backup_system(backup_type="manual")
        if record:
            flash(f"Το backup δημιουργήθηκε: {record.filename}", "success")
        else:
            flash("Αποτυχία δημιουργίας backup.", "danger")
    return redirect(url_for('manage_backups'))

@app.route('/manage_backups')
@login_required
def manage_backups():
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης.", "danger")
        return redirect(url_for('index'))
    backups = BackupRecord.query.order_by(BackupRecord.created_at.desc()).limit(100).all()
    return render_template(
        'manage_backups.html',
        backups=backups,
        retention_count=BACKUP_RETENTION_COUNT,
        include_database=BACKUP_INCLUDE_DATABASE,
    )

@app.route('/backups/create', methods=['POST'])
@login_required
def create_backup_route():
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης.", "danger")
        return redirect(url_for('index'))
    try:
        record = create_backup_record(backup_type="manual")
        flash(f"Το backup δημιουργήθηκε: {record.filename}", "success")
    except Exception as exc:
        flash(f"Αποτυχία δημιουργίας backup: {exc}", "danger")
    return redirect(url_for('manage_backups'))

@app.route('/backups/<int:backup_id>/verify', methods=['POST'])
@login_required
def verify_backup_route(backup_id):
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης.", "danger")
        return redirect(url_for('index'))
    record = BackupRecord.query.get_or_404(backup_id)
    try:
        verify_backup_record(record)
        flash("Το backup επαληθεύτηκε επιτυχώς.", "success")
    except Exception as exc:
        record.status = "VERIFY_FAILED"
        record.verified_at = datetime.utcnow()
        record.verify_message = str(exc)[:255]
        db.session.commit()
        log_action("BACKUP_VERIFY_ERROR", f"BackupRecord:{record.id}", record.verify_message)
        flash(f"Αποτυχία επαλήθευσης backup: {exc}", "danger")
    return redirect(url_for('manage_backups'))

@app.route('/backups/<int:backup_id>/download')
@login_required
def download_backup_route(backup_id):
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης.", "danger")
        return redirect(url_for('index'))
    record = BackupRecord.query.get_or_404(backup_id)
    if not os.path.exists(record.path):
        flash("Το αρχείο backup δεν βρέθηκε στον δίσκο.", "danger")
        return redirect(url_for('manage_backups'))
    log_action("BACKUP_DOWNLOAD", f"BackupRecord:{record.id}", record.filename)
    return send_file(record.path, as_attachment=True, download_name=record.filename)

# --- ΔΙΑΣΥΝΔΕΣΗ ΜΕ ΑΤΛΑΣ ---
def get_atlas_data(amka):
    wsdl_url = 'http://www.idika.gov.gr/webservices/amka/epres_neu/Service.asmx?WSDL'
    USERNAME = os.getenv("ATLAS_USERNAME")
    PASSWORD = os.getenv("ATLAS_PASSWORD")
    try:
        client = Client(wsdl=wsdl_url)
        response_string = client.service.entryPoint(user_ed=USERNAME, password_ed=PASSWORD, input_ed=amka)
        if response_string and "ΛΑΘΟΣ" not in response_string:
            fields = response_string.split('|')
            if len(fields) > 16:
                raw_date = fields[16]
                formatted_date = f"{raw_date[6:8]}/{raw_date[4:6]}/{raw_date[0:4]}" if len(raw_date) == 8 else raw_date
                return {
                    'ΑΜΚΑ': fields[4] if fields[4] else amka, 'ΕΠΩΝΥΜΟ': fields[5], 'ΟΝΟΜΑ': fields[7],
                    'ΠΑΤΡΩΝΥΜΟ': fields[8], 'ΗΜΕΡΟΜΗΝΙΑ_ΓΕΝΝΗΣΗΣ': formatted_date
                }
        return None
    except Exception as e:
        return None

# --- CUSTOM ERROR PAGE & AUDIT LOG ΓΙΑ TO RATE LIMITING (429) ---
@app.errorhandler(429)
def ratelimit_handler(e):
    # 1. Βρίσκουμε την πραγματική IP του χρήστη/bot
    hacker_ip = request.remote_addr
    
    # 2. Το καταγράφουμε στο ISO Audit Log ως "SECURITY_ALERT"
    log_action(
        action="SECURITY_ALERT", 
        target=f"IP: {hacker_ip}", 
        details=f"Αυτόματο μπλοκάρισμα (Rate Limit): {e.description}"
    )
    
    # 3. Του δείχνουμε την όμορφη κόκκινη σελίδα
    return render_template('429.html', error_description=e.description), 429

# --- ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("5 per minute")  # ΕΠΙΤΡΕΠΕΙ ΜΟΝΟ 5 ΠΡΟΣΠΑΘΕΙΕΣ ΤΟ ΛΕΠΤΟ ΑΝΑ IP
def login():
    if request.method == 'GET' and current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            log_action("LOGIN", "System", "Επιτυχής σύνδεση")
            return redirect(url_for('index'))
        log_action("FAILED_LOGIN", "System", f"Αποτυχία: {username}")
        flash('Λάθος Όνομα Χρήστη ή Κωδικός', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    log_action("LOGOUT", "System", "Αποσύνδεση")
    session.pop("sso_user", None)
    logout_user()
    return redirect(url_for('login'))

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if not can_change_password():
        flash("Η αλλαγή κωδικού γίνεται από το κεντρικό SSO.", "warning")
        return redirect(url_for('index'))
    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        user = User.query.get(current_user.id)

        # 1. Έλεγχος αν ο παλιός κωδικός είναι σωστός
        if not check_password_hash(user.password_hash, old_password):
            flash('Ο παλιός κωδικός που εισάγατε δεν είναι σωστός.', 'danger')
            return redirect(url_for('change_password'))

        # 2. Έλεγχος αν οι δύο νέοι κωδικοί ταιριάζουν
        if new_password != confirm_password:
            flash('Οι νέοι κωδικοί δεν ταιριάζουν μεταξύ τους.', 'warning')
            return redirect(url_for('change_password'))

        # 3. Αποθήκευση νέου κωδικού (Κρυπτογραφημένα!)
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        
        # Καταγραφή στο Audit Log
        log_action("PASSWORD_CHANGE", f"Χρήστης: {user.username}", "Επιτυχής αλλαγή κωδικού πρόσβασης")
        
        flash('Ο κωδικός σας άλλαξε επιτυχώς!', 'success')
        return redirect(url_for('index'))

    return render_template('change_password.html')

@app.route('/')
@login_required
@limiter.exempt  # <--- ΠΡΟΣΘΗΚΗ: Εξαιρεί την αρχική σελίδα από το Rate Limiting
def index():
    return render_template('index.html')

# ==============================================================================
# PRG PATTERN: STEP 1 - Υποδοχή δεδομένων (POST)
# ==============================================================================
@app.route('/search', methods=['POST'])
@login_required
@limiter.limit("30 per minute; 300 per day")  # Άνετο για ανθρώπους, κόβει τα bots
def search_post():
    amka = request.form.get('amka')
    
    if not amka:
        flash("Παρακαλώ εισάγετε αριθμό ΑΜΚΑ.", "warning")
        return redirect(url_for('index'))
        
    amka = amka.strip()
    
    # 1. Αποθηκεύουμε το ΑΜΚΑ με ασφάλεια στο κρυπτογραφημένο session της Flask
    session['search_amka'] = amka
    
    # 2. Ανακατεύθυνση (Redirect) στην GET διαδρομή για να μην σκάει το Back button
    return redirect(url_for('search_results'))

# ==============================================================================
# PRG PATTERN: STEP 2 - Επεξεργασία & Προβολή (GET)
# ==============================================================================
@app.route('/search_results', methods=['GET'])
@login_required
def search_results():
    # Ανάκτηση του ΑΜΚΑ από το session
    amka = session.get('search_amka')
    
    # Αν κάποιος μπει απευθείας στο /search_results χωρίς δεδομένα, επιστροφή στην αρχική
    if not amka:
        return redirect(url_for('index'))

    # --- Η ΔΙΚΗ ΣΟΥ ΛΟΓΙΚΗ ΑΚΡΙΒΩΣ ΟΠΩΣ ΗΤΑΝ ---
    person_data = get_atlas_data(amka)
    if not person_data:
        flash(f'Δεν βρέθηκε εγγραφή (ΑΜΚΑ: {amka}).', 'danger')
        return redirect(url_for('index'))
        
    last_exam = Exam.query.filter_by(amka=amka).order_by(Exam.id.desc()).first()
    local_data = {
        'asma': last_exam.asma if last_exam else '', 
        'vathmos': last_exam.vathmos if last_exam else '',
        'monada': last_exam.monada if last_exam else '',
        'kinito': last_exam.kinito if last_exam else ''
    }
    
    # Πηγαίνει ΠΑΝΤΑ στο results.html για νέα εξέταση
    return render_template(
        'results.html',
        person=person_data,
        local_data=local_data,
        categories=get_exam_categories(),
        purposes=get_exam_purposes()
    )
@app.route('/create_exam', methods=['POST'])
@login_required
def create_exam():
    # Παίρνουμε την ημερομηνία από τη φόρμα και τη μετατρέπουμε σωστά
    exam_date_str = request.form.get('exam_date')
    parsed_date = datetime.strptime(exam_date_str, '%Y-%m-%d') if exam_date_str else datetime.utcnow()

    new_exam = Exam(
        asma=request.form.get('asma'), 
        eponymo=request.form.get('eponymo'),
        onoma=request.form.get('onoma'), 
        patronymo=request.form.get('patronymo'),
        vathmos=request.form.get('vathmos'), 
        monada=request.form.get('monada'),
        amka=request.form.get('amka'), 
        hm_gen=request.form.get('hm_gen'),
        kinito=(request.form.get('kinito') or '').strip(),
        skopos=request.form.get('skopos'),
        katigoria=request.form.get('katigoria'), 
        exam_date=parsed_date,  
        created_by=current_user.username
    )
    db.session.add(new_exam)
    db.session.commit()
    log_action("CREATE", f"ID:{new_exam.id}", f"Νέα εξέταση ΑΜΚΑ: {new_exam.amka}")
    flash(f'Η Εξέταση άνοιξε.', 'success')
    return redirect(url_for('update_exam', exam_id=new_exam.id))

@app.route('/download_draft/<int:exam_id>')
@login_required
def download_draft(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    last_exam = Exam.query.filter(Exam.amka == exam.amka, Exam.id < exam.id).order_by(Exam.exam_date.desc()).first()
    last_porisma = last_exam.porisma if last_exam and last_exam.porisma != "ΕΚΚΡΕΜΕΙ" else "ΠΡΩΤΗ ΕΞΕΤΑΣΗ"
    
    template_path = os.path.join(app.config['TEMPLATE_FOLDER'], DRAFT_TEMPLATE_FILE)
    if not os.path.exists(template_path):
        flash(f'Το αρχείο "{DRAFT_TEMPLATE_FILE}" δεν βρέθηκε στον φάκελο templates.', 'danger')
        return redirect(url_for('index'))

    doc = DocxTemplate(template_path)
    doc.render({
        'EXAM_ID': str(exam.id), 'EPONYMO': exam.eponymo, 'ONOMA': exam.onoma, 'PATRONYMO': exam.patronymo,
        'ASMA': exam.asma, 'VATHMOS': exam.vathmos, 
        'KATIGORIA': exam.katigoria,  
        'MONADA': exam.monada, 'AMKA': exam.amka, 'HM_GEN': exam.hm_gen,
        'KINITO': exam.kinito or '',
        'LAST_EXAM': last_porisma, 'SKOPOS': exam.skopos,
        'EXAM_DATE': exam.exam_date.strftime('%d/%m/%Y')  
    })
    
    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    log_action("DOWNLOAD", f"ID:{exam.id}", "Λήψη Word")
    return send_file(file_stream, as_attachment=True, download_name=f"KAI_Draft_{exam.id}.docx")

@app.route('/download_certificate/<int:exam_id>')
@login_required
def download_certificate(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    
    # Αν η εξέταση δεν έχει ολοκληρωθεί, δεν βγάζουμε βεβαίωση
    if exam.porisma == 'ΕΚΚΡΕΜΕΙ':
        flash('Η εξέταση εκκρεμεί. Δεν μπορεί να εκδοθεί βεβαίωση καταλληλότητας ακόμα.', 'warning')
        return redirect(url_for('history', amka=exam.amka))

    template_path = os.path.join(app.config['TEMPLATE_FOLDER'], 'certificate.docx')
    
    try:
        doc = DocxTemplate(template_path)
        # Όλα τα δεδομένα που θα "ταΐσουμε" στο Word
        context = {
            'EXAM_ID': str(exam.id),
            'EPONYMO': exam.eponymo,
            'ONOMA': exam.onoma,
            'PATRONYMO': exam.patronymo,
            'ASMA': exam.asma,
            'VATHMOS': exam.vathmos,
            'KATIGORIA': exam.katigoria,
            'MONADA': exam.monada,
            'AMKA': exam.amka,
            'HM_GEN': exam.hm_gen,
            'EXAM_DATE': exam.exam_date.strftime('%d/%m/%Y'),
            'PORISMA': exam.porisma,
            'VALID_UNTIL': format_display_date(exam.valid_until) if exam.valid_until else '-'
        }
        doc.render(context)
        
        file_stream = io.BytesIO()
        doc.save(file_stream)
        file_stream.seek(0)
        
        # Καταγραφή στο ISO Audit Log
        log_action("DOWNLOAD_CERT", f"ID:{exam.id}", f"Έκδοση Βεβαίωσης για ΑΣΜΑ: {exam.asma}")
        
        # Όνομα αρχείου που θα κατέβει
        filename = f"Βεβαίωση για_{exam.eponymo}_{exam.asma}.docx"
        return send_file(file_stream, as_attachment=True, download_name=filename)
        
    except Exception as e:
        flash(f'Σφάλμα κατά τη δημιουργία βεβαίωσης. Μήπως λείπει το certificate.docx; (Λεπτομέρεια: {str(e)})', 'danger')
        return redirect(url_for('history', amka=exam.amka))

@app.route('/search_by_id', methods=['POST'])
@login_required
def search_by_id():
    exam_id = request.form.get('exam_id').strip()
    exam = Exam.query.filter_by(id=exam_id).first()
    if exam: return redirect(url_for('update_exam', exam_id=exam.id))
    flash(f'Δεν βρέθηκε Εξέταση.', 'danger')
    return redirect(url_for('index'))

@app.route('/download_medical_history')
@login_required
def download_medical_history():
    # Ψάχνει το αρχείο iatriko_istoriko.pdf μέσα στον φάκελο templates!
    file_path = os.path.join(app.config['TEMPLATE_FOLDER'], 'iatriko_istoriko.pdf')
    
    if not os.path.exists(file_path):
        flash('Το αρχείο "iatriko_istoriko.pdf" δεν βρέθηκε στον φάκελο templates. Παρακαλώ προσθέστε το.', 'danger')
        return redirect(url_for('index'))
        
    # Καταγραφή στο Audit Log για λόγους ISO 27799
    log_action("DOWNLOAD_FORM", "System", "Λήψη Κενού Δελτίου Ιατρικού Ιστορικού")
    
    return send_file(file_path, as_attachment=True, download_name="Deltio_Iatrikou_Istorikou.pdf")

@app.route('/pending_exams')
@login_required
def pending_exams():
    # Τραβάμε από τη βάση όσες εξετάσεις έχουν πόρισμα "ΕΚΚΡΕΜΕΙ", ταξινομημένες από την παλαιότερη στη νεότερη
    pending = Exam.query.filter_by(porisma='ΕΚΚΡΕΜΕΙ').order_by(Exam.exam_date.asc()).all()
    return render_template('pending.html', exams=pending)

@app.route('/exam_reports')
@login_required
def exam_reports():
    today = datetime.now().date()
    default_start = today.replace(day=1)
    start_raw = request.args.get('start_date') or default_start.isoformat()
    end_raw = request.args.get('end_date') or today.isoformat()
    selected_skopos = request.args.get('skopos', 'ΕΤΗΣΙΑ')

    try:
        start_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_raw, '%Y-%m-%d').date()
    except ValueError:
        flash('Μη έγκυρο χρονικό διάστημα αναφοράς.', 'danger')
        start_date = default_start
        end_date = today
        start_raw = start_date.isoformat()
        end_raw = end_date.isoformat()

    if start_date > end_date:
        flash('Η ημερομηνία έναρξης δεν μπορεί να είναι μετά την ημερομηνία λήξης.', 'warning')
        start_date, end_date = end_date, start_date
        start_raw = start_date.isoformat()
        end_raw = end_date.isoformat()

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
    query = Exam.query.filter(
        Exam.porisma != 'ΕΚΚΡΕΜΕΙ',
        Exam.exam_date >= start_dt,
        Exam.exam_date < end_dt
    )

    if selected_skopos and selected_skopos != '__all__':
        query = query.filter(Exam.skopos == selected_skopos)

    exams = query.order_by(
        Exam.exam_date.asc(),
        Exam.monada.asc(),
        Exam.eponymo.asc(),
        Exam.onoma.asc()
    ).all()

    return render_template(
        'exam_reports.html',
        exams=exams,
        start_date=start_raw,
        end_date=end_raw,
        selected_skopos=selected_skopos,
        skopoi=get_exam_purposes()
    )

@app.route('/update_exam/<int:exam_id>', methods=['GET', 'POST'])
@login_required
def update_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    
    if request.method == 'POST':
        # Διαβάζουμε τι έστειλε η φόρμα
        new_porisma = request.form.get('porisma')
        new_valid_until = request.form.get('valid_until')
        
        # --- 1. ΕΛΕΓΧΟΣ: ΕΙΝΑΙ ΗΔΗ ΟΛΟΚΛΗΡΩΜΕΝΗ; ---
        # Αν το τρέχον πόρισμα ΔΕΝ είναι 'ΕΚΚΡΕΜΕΙ', τότε η εξέταση έχει κλειδώσει.
        is_locked = (exam.porisma != 'ΕΚΚΡΕΜΕΙ')

        if not is_locked:
            # ΑΛΛΑΓΗ ΜΟΝΟ ΑΝ ΕΙΝΑΙ ΑΝΟΙΧΤΗ: Έλεγχος Ημερομηνίας (Backend Validation)
            if new_valid_until:
                try:
                    valid_date = datetime.strptime(new_valid_until, '%Y-%m-%d').date()
                    today_date = datetime.now().date()
                    if valid_date < today_date:
                        flash("Σφάλμα: Η ημερομηνία λήξης δεν μπορεί να είναι στο παρελθόν!", "danger")
                        return render_template('exam_form.html', exam=exam)
                except ValueError:
                    flash("Μη έγκυρη μορφή ημερομηνίας.", "danger")
                    return render_template('exam_form.html', exam=exam)

            # Ενημέρωση των πεδίων αφού η εξέταση ήταν 'ΕΚΚΡΕΜΕΙ'
            old_porisma = exam.porisma
            exam.porisma = new_porisma
            exam.valid_until = new_valid_until
            log_action("UPDATE", f"ID:{exam.id}", f"Ολοκλήρωση εξέτασης: {new_porisma}")
        else:
            # ΑΝ ΕΙΝΑΙ ΚΛΕΙΔΩΜΕΝΗ: Αγνοούμε τα porisma/valid_until και κρατάμε τα παλιά
            # (Αυτό προστατεύει από πειραγμένα HTML requests)
            pass

        # --- 2. ΠΟΛΛΑΠΛΑ ΑΡΧΕΙΑ (ΕΠΙΤΡΕΠΕΤΑΙ ΠΑΝΤΑ) ---
        scan_files = request.files.getlist('scan_files')
        exam_folder = os.path.join(app.config['UPLOAD_FOLDER'], exam.amka, str(exam.id))
        
        files_saved = False
        for file in scan_files:
            if file and file.filename != '':
                if allowed_file(file.filename):
                    os.makedirs(exam_folder, exist_ok=True)
                    safe_filename = secure_filename(file.filename)
                    file.save(os.path.join(exam_folder, safe_filename))
                    files_saved = True
                    log_action("FILE_UPLOAD", f"ID:{exam.id}", f"Νέο αρχείο: {safe_filename}")
                else:
                    flash(f'Το αρχείο "{file.filename}" απορρίφθηκε. Επιτρέπονται μόνο PDF/Images.', 'danger')
                    log_action("SECURITY_ALERT", f"ID:{exam.id}", f"Απόπειρα upload απαγορευμένου τύπου: {file.filename}")
        
        if files_saved:
            exam.scan_filepath = f"{exam.amka}/{exam.id}"
            
        db.session.commit()
        
        if is_locked and files_saved:
            flash('Προστέθηκαν νέα αρχεία στην ολοκληρωμένη εξέταση.', 'success')
        elif not is_locked:
            flash('Η εξέταση ολοκληρώθηκε και αποθηκεύτηκε.', 'success')
        else:
            flash('Δεν έγιναν αλλαγές.', 'info')
            
        return redirect(url_for('update_exam', exam_id=exam.id))
        
    return render_template('exam_form.html', exam=exam)

@app.route('/delete_exam/<int:exam_id>', methods=['POST'])
@login_required
def delete_exam(exam_id):
    exam = Exam.query.get_or_404(exam_id)
    amka = exam.amka
    
    if exam.porisma == 'ΕΚΚΡΕΜΕΙ':
        log_action("DELETE", f"ID:{exam.id}", f"Διαγραφή εξέτασης")
        db.session.delete(exam)
        db.session.commit()
        flash(f'Η εξέταση διαγράφηκε επιτυχώς.', 'success')
        
    # Έξυπνη ανακατεύθυνση ανάλογα με το από πού έγινε η διαγραφή
    referrer = request.referrer
    if referrer:
        if 'pending_exams' in referrer:
            return redirect(url_for('pending_exams'))
        elif 'update_exam' in referrer:
            return redirect(url_for('history', amka=amka))
            
    return redirect(url_for('history', amka=amka))

@app.route('/history/<amka>')
@login_required
def history(amka):
    exams = Exam.query.filter_by(amka=amka).order_by(Exam.exam_date.desc()).all()
    
    # --- ΝΕΟ: ΕΞΥΠΝΟ ΣΚΑΝΑΡΙΣΜΑ ΦΑΚΕΛΩΝ ---
    exam_files = {}
    for exam in exams:
        folder_path = os.path.join(app.config['UPLOAD_FOLDER'], exam.amka, str(exam.id))
        if os.path.exists(folder_path):
            # Παίρνουμε μια λίστα με όλα τα αρχεία (pdf, jpg, κλπ) μέσα στον φάκελο της εξέτασης
            exam_files[exam.id] = os.listdir(folder_path)
        else:
            exam_files[exam.id] = []
            
    return render_template('history.html', exams=exams, amka=amka, eponymo=exams[0].eponymo if exams else "", onoma=exams[0].onoma if exams else "", exam_files=exam_files)

# --- ΔΙΑΧΕΙΡΙΣΗ ΧΡΗΣΤΩΝ (ΜΟΝΟ ΓΙΑ ADMIN) ---
@app.route('/manage_users')
@login_required
def manage_users():
    # Αυστηρός έλεγχος: Μόνο ο admin μπαίνει εδώ
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης στη διαχείριση χρηστών.", "danger")
        return redirect(url_for('index'))
    
    users = User.query.order_by(User.username.asc()).all()
    return render_template(
        'manage_users.html',
        users=users,
        central_auth=central_auth_context(),
        legacy_user_admin_allowed=local_user_admin_allowed(),
        admin_username=os.getenv("ADMIN_USERNAME", "admin"),
    )

@app.route('/add_user', methods=['POST'])
@login_required
def add_user():
    if not local_user_admin_allowed():
        flash("Η δημιουργία παραγωγικών χρηστών γίνεται πλέον από το κεντρικό Auth/Keycloak. Οι τοπικοί χρήστες είναι μόνο emergency fallback.", "warning")
        return redirect(url_for('manage_users'))
    
    new_username = (request.form.get('new_username') or '').strip()
    new_password = request.form.get('new_password')
    if not new_username or not new_password:
        flash("Συμπληρώστε username και κωδικό για τον τοπικό fallback χρήστη.", "danger")
        return redirect(url_for('manage_users'))
    
    if User.query.filter_by(username=new_username).first():
        flash(f'Ο χρήστης {new_username} υπάρχει ήδη!', 'danger')
    else:
        hashed_pw = generate_password_hash(new_password)
        new_user = User(username=new_username, password_hash=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        log_action("ADD_USER", f"Χρήστης: {new_username}", "Δημιουργία νέου λογαριασμού χρήστη")
        flash(f'Ο χρήστης {new_username} δημιουργήθηκε επιτυχώς.', 'success')
        
    return redirect(url_for('manage_users'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@login_required
def delete_user(user_id):
    if not local_user_admin_allowed():
        flash("Η διαγραφή παραγωγικών χρηστών γίνεται πλέον από το κεντρικό Auth/Keycloak. Οι τοπικοί χρήστες δεν αλλάζουν από SSO session.", "warning")
        return redirect(url_for('manage_users'))
        
    user_to_delete = User.query.get_or_404(user_id)
    
    # Προστασία: Δεν αφήνουμε τον admin να διαγράψει τον εαυτό του!
    if user_to_delete.username == os.getenv("ADMIN_USERNAME", "admin"):
        flash("Αδύνατη η διαγραφή του κεντρικού διαχειριστή του συστήματος!", "danger")
    else:
        del_username = user_to_delete.username
        db.session.delete(user_to_delete)
        db.session.commit()
        log_action("DELETE_USER", f"Χρήστης: {del_username}", "Διαγραφή λογαριασμού χρήστη")
        flash(f'Ο χρήστης {del_username} διαγράφηκε οριστικά.', 'success')
        
    return redirect(url_for('manage_users'))

@app.route('/manage_exam_options')
@login_required
def manage_exam_options():
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης στη διαχείριση λιστών εξέτασης.", "danger")
        return redirect(url_for('index'))

    option_groups = {
        option_type: ExamOption.query.filter_by(option_type=option_type).order_by(
            ExamOption.sort_order.asc(),
            ExamOption.label.asc()
        ).all()
        for option_type in OPTION_TYPE_LABELS
    }
    return render_template(
        'manage_exam_options.html',
        option_groups=option_groups,
        option_type_labels=OPTION_TYPE_LABELS
    )

@app.route('/exam_options/add', methods=['POST'])
@login_required
def add_exam_option():
    if not is_admin_user():
        return redirect(url_for('index'))

    option_type = request.form.get('option_type')
    label = (request.form.get('label') or '').strip()
    sort_order = _parse_sort_order(
        request.form.get('sort_order'),
        _next_exam_option_sort_order(option_type)
    )

    if option_type not in OPTION_TYPE_LABELS:
        flash("Μη έγκυρος τύπος λίστας.", "danger")
        return redirect(url_for('manage_exam_options'))
    if not label:
        flash("Η επιλογή δεν μπορεί να είναι κενή.", "danger")
        return redirect(url_for('manage_exam_options'))
    if ExamOption.query.filter_by(option_type=option_type, label=label).first():
        flash("Η επιλογή υπάρχει ήδη στη συγκεκριμένη λίστα.", "warning")
        return redirect(url_for('manage_exam_options'))

    option = ExamOption(
        option_type=option_type,
        label=label,
        sort_order=sort_order,
        is_active=True
    )
    db.session.add(option)
    db.session.commit()
    log_action("ADD_EXAM_OPTION", OPTION_TYPE_LABELS[option_type], label)
    flash("Η επιλογή προστέθηκε.", "success")
    return redirect(url_for('manage_exam_options'))

@app.route('/exam_options/<int:option_id>/update', methods=['POST'])
@login_required
def update_exam_option(option_id):
    if not is_admin_user():
        return redirect(url_for('index'))

    option = ExamOption.query.get_or_404(option_id)
    label = (request.form.get('label') or '').strip()
    sort_order = _parse_sort_order(request.form.get('sort_order'), option.sort_order)
    is_active = request.form.get('is_active') == 'on'

    if not label:
        flash("Η επιλογή δεν μπορεί να είναι κενή.", "danger")
        return redirect(url_for('manage_exam_options'))

    duplicate = ExamOption.query.filter_by(option_type=option.option_type, label=label).first()
    if duplicate and duplicate.id != option.id:
        flash("Υπάρχει ήδη επιλογή με αυτό το όνομα στη συγκεκριμένη λίστα.", "warning")
        return redirect(url_for('manage_exam_options'))

    old_label = option.label
    option.label = label
    option.sort_order = sort_order
    option.is_active = is_active
    db.session.commit()
    log_action("UPDATE_EXAM_OPTION", OPTION_TYPE_LABELS[option.option_type], f"{old_label} -> {label}")
    flash("Η επιλογή ενημερώθηκε.", "success")
    return redirect(url_for('manage_exam_options'))

@app.route('/exam_options/<int:option_id>/delete', methods=['POST'])
@login_required
def delete_exam_option(option_id):
    if not is_admin_user():
        return redirect(url_for('index'))

    option = ExamOption.query.get_or_404(option_id)
    option_type_label = OPTION_TYPE_LABELS.get(option.option_type, option.option_type)
    label = option.label
    db.session.delete(option)
    db.session.commit()
    log_action("DELETE_EXAM_OPTION", option_type_label, label)
    flash("Η επιλογή διαγράφηκε από τη λίστα.", "success")
    return redirect(url_for('manage_exam_options'))

@app.route('/exam_options/reseed_defaults', methods=['POST'])
@login_required
def reseed_exam_options():
    if not is_admin_user():
        return redirect(url_for('index'))

    _seed_exam_options()
    log_action("RESEED_EXAM_OPTIONS", "System", "Επαναφορά προεπιλεγμένων επιλογών που έλειπαν")
    flash("Οι προεπιλεγμένες επιλογές που έλειπαν επαναφέρθηκαν.", "success")
    return redirect(url_for('manage_exam_options'))

@app.route('/audit_logs')
@login_required
def audit_logs():
    if not is_admin_user():
        flash("Δεν έχετε δικαίωμα πρόσβασης.", "danger")
        return redirect(url_for('index'))
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()
    return render_template('audit.html', logs=logs)

# ==============================================================================
# HL7 FHIR INTEROPERABILITY API (Standard: FHIR R4)
# ==============================================================================
# ==============================================================================
# UPGRADED HL7 FHIR API: FULL MEDICAL HISTORY (Bundle)
# ==============================================================================
@app.route('/api/fhir/Patient/<amka>', methods=['GET'])
@login_required
def fhir_get_patient_history(amka):
    """
    Εξάγει το πλήρες ιστορικό του ασθενούς σε μορφή HL7 FHIR Bundle.
    Περιλαμβάνει Δημογραφικά Στοιχεία και όλες τις Εξετάσεις (DiagnosticReports).
    """
    # 1. Ανάκτηση όλων των εξετάσεων από τη βάση, ταξινομημένες ανά ημερομηνία
    all_exams = Exam.query.filter_by(amka=amka).order_by(Exam.exam_date.desc()).all()
    
    if not all_exams:
        return jsonify({
            "resourceType": "OperationOutcome", 
            "issue": [{"severity": "error", "code": "not-found", "diagnostics": "No history found for this AMKA"}]
        }), 404

    # 2. Δημιουργία του Bundle (Το "πακέτο" των δεδομένων)
    fhir_bundle = {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(all_exams),
        "entry": []
    }

    # 3. Προσθήκη του Patient Resource (Δημογραφικά) ως πρώτη εγγραφή
    patient_resource = {
        "fullUrl": f"http://localhost/api/fhir/Patient/{amka}",
        "resource": {
            "resourceType": "Patient",
            "id": amka,
            "identifier": [
                {"system": "http://idika.gov.gr/amka", "value": amka},
                {"system": "http://haf.gr/asma", "value": all_exams[0].asma}
            ],
            "name": [{"use": "official", "family": all_exams[0].eponymo, "given": [all_exams[0].onoma]}],
            "birthDate": all_exams[0].hm_gen
        }
    }
    fhir_bundle["entry"].append(patient_resource)

    # 4. Προσθήκη κάθε εξέτασης ως DiagnosticReport Resource
    for exam in all_exams:
        report_resource = {
            "fullUrl": f"http://localhost/api/fhir/DiagnosticReport/{exam.id}",
            "resource": {
                "resourceType": "DiagnosticReport",
                "id": str(exam.id),
                "status": "final" if exam.porisma != "ΕΚΚΡΕΜΕΙ" else "preliminary",
                "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0074", "code": "GE", "display": "General Health"}]}],
                "subject": {"reference": f"Patient/{amka}"},
                "effectiveDateTime": exam.exam_date.isoformat(),
                "issued": exam.exam_date.isoformat(),
                "conclusion": exam.porisma,  # Το Πόρισμα
                "extension": [
                    {
                        "url": "http://kai.gr/fhir/StructureDefinition/exam-purpose",
                        "valueString": exam.skopos  # Ο Σκοπός Προσέλευσης
                    },
                    {
                        "url": "http://kai.gr/fhir/StructureDefinition/unit",
                        "valueString": exam.monada  # Η Μονάδα τότε
                    },
                    {
                        "url": "http://kai.gr/fhir/StructureDefinition/rank",
                        "valueString": exam.vathmos  # Ο Βαθμός τότε
                    }
                ]
            }
        }
        fhir_bundle["entry"].append(report_resource)

    # 5. Καταγραφή στο Audit Log
    log_action("FHIR_FULL_HISTORY_EXPORT", f"AMKA: {amka}", f"Εξαγωγή πλήρους ιστορικού ({len(all_exams)} εγγραφές) μέσω HL7 FHIR")

    return jsonify(fhir_bundle), 200

# ==============================================================================
# LEGACY HL7 v2.5 EXPORT: FULL HISTORY
# ==============================================================================
@app.route('/api/hl7/legacy/export/<amka>', methods=['GET'])
@login_required
def export_legacy_hl7_history(amka):
    """
    Παράγει ένα μήνυμα HL7 v2.5 (ORU^R01) που περιέχει όλο το ιστορικό 
    εξετάσεων του ασθενούς για αποστολή σε παλαιότερα HIS.
    """
    # 1. Ανάκτηση δεδομένων
    all_exams = Exam.query.filter_by(amka=amka).order_by(Exam.exam_date.desc()).all()
    
    if not all_exams:
        return "ERROR: No history found", 404

    ts = datetime.now().strftime('%Y%m%d%H%M%S')
    p = all_exams[0] # Στοιχεία ασθενούς από την πιο πρόσφατη εγγραφή

    # 2. Κατασκευή MSH (Header) και PID (Patient Info)
    # Χρησιμοποιούμε \r (Carriage Return) ως standard διαχωριστικό segments
    segments = []
    segments.append(f"MSH|^~\\&|KAI_SYSTEM|KAI|HIS_RECEIVER|HOSPITAL|{ts}||ORU^R01|{ts}|P|2.5|||||UNICODE UTF-8")
    segments.append(f"PID|1||{p.amka}^^^AMKA^AMKA||{p.eponymo}^{p.onoma}||{p.hm_gen.replace('/', '')}|M")

    # 3. Προσθήκη κάθε εξέτασης ως ζεύγος OBR/OBX
    # OBR: Στοιχεία της εξέτασης (Ημερομηνία, ID)
    # OBX: Το αποτέλεσμα (Πόρισμα, Σκοπός)
    for i, exam in enumerate(all_exams, 1):
        exam_ts = exam.exam_date.strftime('%Y%m%d%H%M%S')
        
        # OBR - Observation Request Segment
        segments.append(f"OBR|{i}|{exam.id}|{exam.id}|KAI_EXAM^MEDICAL_EXAM^L|||{exam_ts}")
        
        # OBX 1 - Το Πόρισμα (Conclusion)
        segments.append(f"OBX|1|TX|PORISMA^CONCLUSION^L||{exam.porisma}||||||F")
        
        # OBX 2 - Ο Σκοπός (Purpose)
        segments.append(f"OBX|2|TX|SKOPOS^PURPOSE^L||{exam.skopos}||||||F")
        
        # OBX 3 - Στρατιωτικά στοιχεία τότε (Rank/Unit)
        segments.append(f"OBX|3|TX|MIL_DATA^RANK_UNIT^L||{exam.vathmos} - {exam.monada}||||||F")

    # 4. Καταγραφή εξαγωγής στο Audit Log
    log_action("HL7_V2_EXPORT", amka, f"Εξαγωγή ιστορικού ({len(all_exams)} εγγραφές) σε Legacy HL7")

    # Επιστροφή του μηνύματος ως απλό κείμενο
    return "\r".join(segments), 200, {'Content-Type': 'text/plain; charset=utf-8'}

@app.route('/manual')
@login_required
def manual():
    # Καταγράφουμε στο Audit Log ότι ο χρήστης άνοιξε το εγχειρίδιο
    log_action("VIEW_MANUAL", "System", "Προβολή Εγχειριδίου Χρήσης")
    return render_template('manual.html')

@app.route('/his_system')
@login_required
def his_system():
    # Φορτώνει τη σελίδα που περιέχει το RDP
    return render_template('his_system.html')

@app.route('/appointments')
@login_required
def appointments():
    # Καταγραφή στο Audit Log για ιχνηλασιμότητα ISO 27799
    if APPOINTMENTS_FEATURE_ENABLED:
        log_action("VIEW_APPOINTMENTS", "System", "Προβολή συστήματος ηλεκτρονικών ραντεβού (opsyed)")
    else:
        log_action("VIEW_APPOINTMENTS_DISABLED", "System", "Appointments feature is temporarily disabled")
    return render_template('appointments.html')

if __name__ == '__main__':
    # Προειδοποίηση αν κάποιος πάει να το τρέξει τοπικά χωρίς Docker/Gunicorn
    print("ΠΡΟΣΟΧΗ: Εκτελείτε τον Development Server. Για παραγωγή, χρησιμοποιήστε Docker Compose.")
    app.run(host='0.0.0.0', port=5000)
