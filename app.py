from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
import os
import json
import hmac
import secrets
import email
import email.policy
import email.utils
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from sqlalchemy import text, func, select, insert, inspect

# Initialize Flask app
app = Flask(__name__, instance_relative_config=True)
# Session secret comes from the SECRET_KEY environment variable (set it on Render).
# Locally, a random key is used if none is set (you'll just get logged out on restart).
import sys
print(f"[nixapp] startup: RENDER={'yes' if os.environ.get('RENDER') else 'no'}, "
      f"SECRET_KEY={'set' if os.environ.get('SECRET_KEY') else 'MISSING'}, "
      f"ADMINS={'set' if os.environ.get('ADMINS') else 'MISSING'}", file=sys.stderr, flush=True)
_secret = os.environ.get("SECRET_KEY", "").strip()
if not _secret:
    print("[nixapp] WARNING: SECRET_KEY not set; using a temporary random key", file=sys.stderr, flush=True)
    _secret = os.urandom(32).hex()
app.secret_key = _secret
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Show times in Eastern time (EST/EDT). Times are stored in the database as UTC.
try:
    from zoneinfo import ZoneInfo
    EASTERN = ZoneInfo("America/New_York")
except Exception:
    from datetime import timedelta
    EASTERN = timezone(timedelta(hours=-5), "EST")

def to_eastern(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN)

@app.template_filter("est")
def est_filter(dt, fmt="%b %d, %Y %I:%M %p"):
    """Usage in templates: {{ some_time|est }}  ->  'Nov 19, 2026 01:21 PM EST'"""
    local = to_eastern(dt)
    if local is None:
        return ""
    return f"{local.strftime(fmt)} {local.strftime('%Z')}"

# Database: use DATABASE_URL (free hosted Postgres) if set, otherwise a local SQLite file
_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    # use the psycopg2 driver (the one in requirements.txt)
    for _prefix in ("postgres://", "postgresql://"):
        if _db_url.startswith(_prefix):
            _db_url = "postgresql+psycopg2://" + _db_url[len(_prefix):]
            break
    app.config["SQLALCHEMY_DATABASE_URI"] = _db_url
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
    print("[nixapp] database: hosted Postgres (DATABASE_URL)", file=sys.stderr, flush=True)
elif os.path.isdir("/mnt/data") and os.access("/mnt/data", os.W_OK):
    # Render persistent disk is attached
    db_path = "/mnt/data/inventory.db"
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    print("[nixapp] database: SQLite on persistent disk (/mnt/data)", file=sys.stderr, flush=True)
else:
    db_path = os.path.join(app.instance_path, "inventory.db")
    os.makedirs(app.instance_path, exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
    if os.environ.get("RENDER"):
        print("[nixapp] WARNING: no DATABASE_URL and no disk; using a temporary SQLite file (changes will be lost on redeploy)", file=sys.stderr, flush=True)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Production cookie settings
app.config["SESSION_COOKIE_DOMAIN"] = "nixapp.org"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Initialize database
db = SQLAlchemy(app)

# Accounts come from environment variables, as comma-separated username:hash pairs:
#   ADMINS = username:hash,username2:hash2   (full admins: inventory, downloads, email)
#   USERS  = username:hash,username2:hash2   (level-2 users: email pages only)
# Make a hash with:
#   python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('NEW_PASSWORD'))"
# If neither is set, nobody can log in.
def _load_accounts(var_name):
    accounts = {}
    for entry in os.environ.get(var_name, "").split(","):
        name, sep, pw_hash = entry.strip().partition(":")
        if name and sep and pw_hash:
            accounts[name] = pw_hash
    return accounts

ADMINS = _load_accounts("ADMINS")
USERS = _load_accounts("USERS")
# If a name appears in both, the admin account wins.
USERS = {name: h for name, h in USERS.items() if name not in ADMINS}
print(f"[nixapp] admin accounts loaded: {sorted(ADMINS)}", file=sys.stderr, flush=True)
print(f"[nixapp] user accounts loaded: {sorted(USERS)}", file=sys.stderr, flush=True)

# Database model
class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, default=0.0)
    url = db.Column(db.String(255))

class EmailMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    to_addr = db.Column(db.String(320), nullable=False, index=True)
    from_addr = db.Column(db.String(320))
    subject = db.Column(db.String(500))
    received_at = db.Column(db.DateTime, nullable=False, index=True)
    text_body = db.Column(db.Text)
    html_body = db.Column(db.Text)
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    is_archived = db.Column(db.Boolean, default=False, nullable=False)
    message_id = db.Column(db.String(500))

class SentEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    from_addr = db.Column(db.String(320), nullable=False)
    to_addrs = db.Column(db.String(2000), nullable=False)
    subject = db.Column(db.String(500))
    text_body = db.Column(db.Text)
    sent_at = db.Column(db.DateTime, nullable=False, index=True)
    provider_id = db.Column(db.String(200))

# Create tables, and load inventory_seed.json the first time the database is empty
def _init_db():
    seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory_seed.json")
    with app.app_context():
        with db.engine.begin() as conn:
            if conn.dialect.name == "postgresql":
                # stops two gunicorn workers from seeding at the same time
                conn.execute(text("SELECT pg_advisory_xact_lock(482611)"))
            db.metadata.create_all(conn)
            table = InventoryItem.__table__
            count = conn.execute(select(func.count()).select_from(table)).scalar()
            if count == 0 and os.path.exists(seed_path):
                with open(seed_path, encoding="utf-8") as f:
                    items = json.load(f)
                rows = [{
                    "name": i["name"],
                    "quantity": int(i["quantity"]),
                    "price": float(i.get("price") or 0),
                    "url": i.get("url") or "",
                } for i in items]
                if rows:
                    conn.execute(insert(table), rows)
                print(f"[nixapp] loaded {len(rows)} items from inventory_seed.json", file=sys.stderr, flush=True)

    # Add columns that were introduced after the email table was first created
    with app.app_context():
        try:
            with db.engine.begin() as conn:
                existing = {c["name"] for c in inspect(conn).get_columns("email_message")}
                false_default = "false" if conn.dialect.name == "postgresql" else "0"
                if "is_archived" not in existing:
                    conn.execute(text(f"ALTER TABLE email_message ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT {false_default}"))
                    print("[nixapp] added column email_message.is_archived", file=sys.stderr, flush=True)
                if "message_id" not in existing:
                    conn.execute(text("ALTER TABLE email_message ADD COLUMN message_id VARCHAR(500)"))
                    print("[nixapp] added column email_message.message_id", file=sys.stderr, flush=True)
        except Exception as e:
            # another worker may have added the column at the same moment
            print(f"[nixapp] column check: {e}", file=sys.stderr, flush=True)

_init_db()

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/inventory')
def inventory():
    is_admin = session.get('user_type') == 'admin'
    username = session.get('username') if is_admin else None
    return render_template('inventory.html', is_admin=is_admin, username=username)

@app.route('/api/inventory')
def api_inventory():
    items = InventoryItem.query.all()
    return jsonify([{ 'id': item.id, 'name': item.name, 'quantity': item.quantity, 'price': item.price, 'url': item.url } for item in items])

@app.route('/links')
def links():
    # List of links to display
    links_list = [
        {"name": "NixApp Home", "url": "https://nixapp.org"},
        {"name": "GitHub", "url": "https://github.com/yourusername"},
        {"name": "Reddit", "url": "https://www.reddit.com/user/yourusername"},
        {"name": "Contact", "url": "mailto:contact@nixapp.org"}
    ]
    return render_template('links.html', links=links_list)

@app.route('/api/update', methods=['POST'])
def update_item():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    item = InventoryItem.query.get(data['id'])
    if not item:
        return jsonify({'error': 'Item not found'}), 404

    item.name = data['name']
    item.quantity = data['quantity']
    item.price = data.get('price', 0)
    item.url = data.get('url', "")
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/downloads', methods=['GET', 'POST'])
def downloads():
    is_admin = session.get('user_type') == 'admin'
    upload_dir = os.path.join('static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    if request.method == 'POST':
        if not is_admin:
            flash('You must be an admin to upload.')
            return redirect(url_for('login'))
        uploaded_file = request.files['file']
        safe_name = secure_filename(uploaded_file.filename) if uploaded_file else ''
        if safe_name:
            filepath = os.path.join(upload_dir, safe_name)
            uploaded_file.save(filepath)
            flash('File uploaded successfully!')
        else:
            flash('Invalid file name.')

    files = os.listdir(upload_dir)
    return render_template('downloads.html', is_admin=is_admin, files=files)

# <-- Added: Delete uploaded file route
@app.route('/delete-file', methods=['POST'])
def delete_file():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    filename = request.form.get('filename')
    if not filename:
        flash('No file specified.')
        return redirect(url_for('downloads'))

    filename = secure_filename(filename)
    if not filename:
        flash('Invalid file name.')
        return redirect(url_for('downloads'))

    file_path = os.path.join('static', 'uploads', filename)
    if os.path.exists(file_path):
        os.remove(file_path)
        flash(f'File "{filename}" deleted successfully.')
    else:
        flash(f'File "{filename}" not found.')

    return redirect(url_for('downloads'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_type') in ('admin', 'user'):
        return redirect(url_for('home'))

    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']

        if user in ADMINS and check_password_hash(ADMINS[user], password):
            session['user_type'] = 'admin'
            session['username'] = user
            session.permanent = True
            return redirect(url_for('home'))
        elif user in USERS and check_password_hash(USERS[user], password):
            session['user_type'] = 'user'
            session['username'] = user
            session.permanent = True
            return redirect(url_for('home'))
        else:
            error = "Invalid credentials. Please try again."

    return render_template('login.html', error=error)

@app.route('/api/add', methods=['POST'])
def api_add_item():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    new_item = InventoryItem(
        name=data.get("name"),
        quantity=data.get("quantity"),
        price=data.get("price", 0),
        url=data.get("url", "")
    )
    db.session.add(new_item)
    db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/api/delete', methods=['POST'])
def api_delete_item():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    item = InventoryItem.query.get(data.get("id"))
    if item:
        db.session.delete(item)
        db.session.commit()

    return jsonify({'status': 'success'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/stremioguide')
def stremioguide():
    return render_template('stremioguide.html')


# ---------------- Email inbox ---------------- #
# Cloudflare Email Routing (catch-all) -> Email Worker -> POST /api/inbound-email
# The worker must send the same secret as the INBOUND_EMAIL_SECRET environment variable.
EMAIL_DOMAIN = os.environ.get("EMAIL_DOMAIN", "nixapp.org")
MAX_INBOUND_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_BODY_CHARS = 500_000

def _is_admin():
    return session.get('user_type') == 'admin'

def _is_staff():
    # Full admins and level-2 users can both reach the email pages.
    return session.get('user_type') in ('admin', 'user')

def _clean_addr(value, limit=320):
    _, addr = email.utils.parseaddr(value or "")
    return (addr or value or "").strip().lower()[:limit]

def _extract_bodies(msg):
    text_body, html_body = None, None
    try:
        part = msg.get_body(preferencelist=('plain',))
        if part is not None:
            text_body = part.get_content()
    except Exception:
        pass
    try:
        part = msg.get_body(preferencelist=('html',))
        if part is not None:
            html_body = part.get_content()
    except Exception:
        pass
    if text_body:
        text_body = text_body[:MAX_BODY_CHARS]
    if html_body:
        html_body = html_body[:MAX_BODY_CHARS]
    return text_body, html_body

@app.route('/api/inbound-email', methods=['POST'])
def inbound_email():
    expected = os.environ.get("INBOUND_EMAIL_SECRET", "")
    provided = request.headers.get("X-Inbound-Secret", "")
    if not expected:
        return jsonify({'error': 'Inbound email not configured'}), 503
    if not hmac.compare_digest(provided.encode(), expected.encode()):
        return jsonify({'error': 'Unauthorized'}), 403
    if (request.content_length or 0) > MAX_INBOUND_BYTES:
        return jsonify({'error': 'Too large'}), 413

    raw = request.get_data(cache=False)
    if not raw or len(raw) > MAX_INBOUND_BYTES:
        return jsonify({'error': 'Bad request'}), 400

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    to_addr = _clean_addr(request.headers.get("X-Envelope-To") or msg.get('To', ''))
    from_addr = _clean_addr(request.headers.get("X-Envelope-From") or msg.get('From', ''))
    subject = str(msg.get('Subject', '') or '')[:500]
    text_body, html_body = _extract_bodies(msg)

    if not to_addr:
        return jsonify({'error': 'No recipient'}), 400

    db.session.add(EmailMessage(
        to_addr=to_addr,
        from_addr=from_addr,
        subject=subject,
        received_at=datetime.now(timezone.utc).replace(tzinfo=None),
        text_body=text_body,
        html_body=html_body,
        message_id=str(msg.get('Message-ID', '') or '').strip()[:500] or None,
    ))
    db.session.commit()
    return jsonify({'status': 'stored'})

@app.route('/inbox')
def inbox():
    if not _is_staff():
        return redirect(url_for('login'))
    rows = db.session.query(
        EmailMessage.to_addr,
        func.count(EmailMessage.id),
        func.max(EmailMessage.received_at),
        func.sum(db.case((EmailMessage.is_read == False, 1), else_=0)),
    ).filter(EmailMessage.is_archived == False).group_by(EmailMessage.to_addr).order_by(func.max(EmailMessage.received_at).desc()).all()
    addresses = [{'addr': r[0], 'count': r[1], 'latest': r[2], 'unread': int(r[3] or 0)} for r in rows]
    new_address = f"{secrets.token_hex(5)}@{EMAIL_DOMAIN}"
    archived_count = EmailMessage.query.filter_by(is_archived=True).count()
    return render_template('inbox.html', addresses=addresses, new_address=new_address, domain=EMAIL_DOMAIN,
                           archived_count=archived_count, can_send=bool(os.environ.get("RESEND_API_KEY")))

@app.route('/inbox/address/<path:addr>')
def inbox_address(addr):
    if not _is_staff():
        return redirect(url_for('login'))
    addr = addr.strip().lower()
    messages = EmailMessage.query.filter_by(to_addr=addr, is_archived=False).order_by(EmailMessage.received_at.desc()).all()
    return render_template('inbox_address.html', addr=addr, messages=messages)

@app.route('/inbox/message/<int:msg_id>')
def inbox_message(msg_id):
    if not _is_staff():
        return redirect(url_for('login'))
    msg = db.session.get(EmailMessage, msg_id)
    if not msg:
        return redirect(url_for('inbox'))
    if not msg.is_read:
        msg.is_read = True
        db.session.commit()
    return render_template('inbox_message.html', msg=msg, can_send=bool(os.environ.get("RESEND_API_KEY")))

@app.route('/inbox/message/<int:msg_id>/delete', methods=['POST'])
def inbox_delete_message(msg_id):
    if not _is_staff():
        return jsonify({'error': 'Unauthorized'}), 403
    msg = db.session.get(EmailMessage, msg_id)
    addr = msg.to_addr if msg else None
    if msg:
        db.session.delete(msg)
        db.session.commit()
    if addr and EmailMessage.query.filter_by(to_addr=addr, is_archived=False).count():
        return redirect(url_for('inbox_address', addr=addr))
    return redirect(url_for('inbox'))

@app.route('/inbox/address/<path:addr>/delete', methods=['POST'])
def inbox_delete_address(addr):
    if not _is_staff():
        return jsonify({'error': 'Unauthorized'}), 403
    EmailMessage.query.filter_by(to_addr=addr.strip().lower()).delete()
    db.session.commit()
    return redirect(url_for('inbox'))

@app.route('/inbox/message/<int:msg_id>/archive', methods=['POST'])
def inbox_archive_message(msg_id):
    if not _is_staff():
        return jsonify({'error': 'Unauthorized'}), 403
    msg = db.session.get(EmailMessage, msg_id)
    if not msg:
        return redirect(url_for('inbox'))
    msg.is_archived = True
    msg.is_read = True
    db.session.commit()
    flash('Email archived.')
    if EmailMessage.query.filter_by(to_addr=msg.to_addr, is_archived=False).count():
        return redirect(url_for('inbox_address', addr=msg.to_addr))
    return redirect(url_for('inbox'))

@app.route('/inbox/message/<int:msg_id>/unarchive', methods=['POST'])
def inbox_unarchive_message(msg_id):
    if not _is_staff():
        return jsonify({'error': 'Unauthorized'}), 403
    msg = db.session.get(EmailMessage, msg_id)
    if msg:
        msg.is_archived = False
        db.session.commit()
        flash('Moved back to inbox.')
    return redirect(url_for('inbox_archived'))

@app.route('/inbox/archive-all', methods=['POST'])
def inbox_archive_all():
    if not _is_staff():
        return jsonify({'error': 'Unauthorized'}), 403
    updated = EmailMessage.query.filter_by(is_archived=False).update(
        {EmailMessage.is_archived: True, EmailMessage.is_read: True},
        synchronize_session=False,
    )
    db.session.commit()
    if updated:
        flash(f'Archived {updated} email{"s" if updated != 1 else ""}.')
    else:
        flash('No emails to archive.')
    return redirect(url_for('inbox'))

@app.route('/inbox/archived')
def inbox_archived():
    if not _is_staff():
        return redirect(url_for('login'))
    messages = EmailMessage.query.filter_by(is_archived=True).order_by(EmailMessage.received_at.desc()).all()
    return render_template('inbox_archived.html', messages=messages)

# ---------------- Sending (Resend) ---------------- #
# Needs RESEND_API_KEY on Render, and nixapp.org verified in Resend.
_LOCAL_PART_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,63}$")
MAX_RECIPIENTS = 10

def _parse_recipients(value):
    addrs = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        _, addr = email.utils.parseaddr(part)
        addr = addr.strip()
        if not addr or "@" not in addr or " " in addr or len(addr) > 320:
            raise ValueError(f'"{part}" is not a valid email address')
        addrs.append(addr)
    if not addrs:
        raise ValueError("Add at least one recipient")
    if len(addrs) > MAX_RECIPIENTS:
        raise ValueError(f"At most {MAX_RECIPIENTS} recipients")
    return addrs

def _send_via_resend(from_addr, to_addrs, subject, text_body, in_reply_to=None):
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Sending isn't set up yet (RESEND_API_KEY is missing on Render).")
    payload = {"from": from_addr, "to": to_addrs, "subject": subject, "text": text_body}
    if in_reply_to:
        payload["headers"] = {"In-Reply-To": in_reply_to, "References": in_reply_to}
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "nixapp"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:
            pass
        raise RuntimeError(f"Resend refused the email: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach Resend: {e.reason}")

@app.route('/inbox/compose', methods=['GET', 'POST'])
def inbox_compose():
    if not _is_staff():
        return redirect(url_for('login'))
    can_send = bool(os.environ.get("RESEND_API_KEY"))
    error = None
    reply_to_id = request.values.get('reply_to', type=int)
    original = db.session.get(EmailMessage, reply_to_id) if reply_to_id else None

    if request.method == 'POST':
        from_local = (request.form.get('from_local') or '').strip().lower()
        to_raw = request.form.get('to', '')
        subject = (request.form.get('subject') or '').strip()[:500]
        body = (request.form.get('body') or '')[:MAX_BODY_CHARS]
        try:
            if not _LOCAL_PART_RE.match(from_local):
                raise ValueError("The From name can only use letters, numbers, dots, dashes, underscores and +")
            if not subject:
                raise ValueError("Add a subject")
            to_addrs = _parse_recipients(to_raw)
            from_addr = f"{from_local}@{EMAIL_DOMAIN}"
            result = _send_via_resend(from_addr, to_addrs, subject, body,
                                      in_reply_to=original.message_id if original else None)
            db.session.add(SentEmail(
                from_addr=from_addr,
                to_addrs=", ".join(to_addrs)[:2000],
                subject=subject,
                text_body=body,
                sent_at=datetime.now(timezone.utc).replace(tzinfo=None),
                provider_id=str(result.get("id", ""))[:200] or None,
            ))
            db.session.commit()
            flash(f"Sent from {from_addr}.")
            return redirect(url_for('inbox_sent'))
        except (ValueError, RuntimeError) as e:
            error = str(e)
        form = {'from_local': from_local, 'to': to_raw, 'subject': subject, 'body': body}
    else:
        if original:
            subj = original.subject or ''
            quoted = "\n".join("> " + line for line in (original.text_body or '').splitlines())
            form = {
                'from_local': original.to_addr.split('@')[0],
                'to': original.from_addr or '',
                'subject': subj if subj.lower().startswith('re:') else f"Re: {subj}",
                'body': f"\n\nOn {est_filter(original.received_at)}, {original.from_addr} wrote:\n{quoted}",
            }
        else:
            from_q = (request.args.get('from') or '').strip().lower()
            form = {
                'from_local': from_q.split('@')[0] if from_q else secrets.token_hex(5),
                'to': request.args.get('to', ''),
                'subject': '',
                'body': '',
            }
    return render_template('inbox_compose.html', form=form, error=error, domain=EMAIL_DOMAIN,
                           can_send=can_send, original=original)

@app.route('/inbox/sent')
def inbox_sent():
    if not _is_staff():
        return redirect(url_for('login'))
    sent = SentEmail.query.order_by(SentEmail.sent_at.desc()).limit(200).all()
    return render_template('inbox_sent.html', sent=sent)

# ---------------------------- #

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5050)
