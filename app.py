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
from datetime import datetime, timezone
from sqlalchemy import text, func, select, insert

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

# Admin accounts come from the ADMINS environment variable, as hashed passwords:
#   ADMINS = username:hash,username2:hash2
# Make a hash with:
#   python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('NEW_PASSWORD'))"
# If ADMINS is not set, nobody can log in.
def _load_admins():
    admins = {}
    for entry in os.environ.get("ADMINS", "").split(","):
        name, sep, pw_hash = entry.strip().partition(":")
        if name and sep and pw_hash:
            admins[name] = pw_hash
    return admins

ADMINS = _load_admins()
print(f"[nixapp] admin accounts loaded: {sorted(ADMINS)}", file=sys.stderr, flush=True)

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
    if session.get('user_type') == 'admin':
        return redirect(url_for('inventory'))

    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']

        if user in ADMINS and check_password_hash(ADMINS[user], password):
            session['user_type'] = 'admin'
            session['username'] = user
            session.permanent = True
            return redirect(url_for('inventory'))
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
    ))
    db.session.commit()
    return jsonify({'status': 'stored'})

@app.route('/inbox')
def inbox():
    if not _is_admin():
        return redirect(url_for('login'))
    rows = db.session.query(
        EmailMessage.to_addr,
        func.count(EmailMessage.id),
        func.max(EmailMessage.received_at),
        func.sum(db.case((EmailMessage.is_read == False, 1), else_=0)),
    ).group_by(EmailMessage.to_addr).order_by(func.max(EmailMessage.received_at).desc()).all()
    addresses = [{'addr': r[0], 'count': r[1], 'latest': r[2], 'unread': int(r[3] or 0)} for r in rows]
    new_address = f"{secrets.token_hex(5)}@{EMAIL_DOMAIN}"
    return render_template('inbox.html', addresses=addresses, new_address=new_address, domain=EMAIL_DOMAIN)

@app.route('/inbox/address/<path:addr>')
def inbox_address(addr):
    if not _is_admin():
        return redirect(url_for('login'))
    addr = addr.strip().lower()
    messages = EmailMessage.query.filter_by(to_addr=addr).order_by(EmailMessage.received_at.desc()).all()
    return render_template('inbox_address.html', addr=addr, messages=messages)

@app.route('/inbox/message/<int:msg_id>')
def inbox_message(msg_id):
    if not _is_admin():
        return redirect(url_for('login'))
    msg = db.session.get(EmailMessage, msg_id)
    if not msg:
        return redirect(url_for('inbox'))
    if not msg.is_read:
        msg.is_read = True
        db.session.commit()
    return render_template('inbox_message.html', msg=msg)

@app.route('/inbox/message/<int:msg_id>/delete', methods=['POST'])
def inbox_delete_message(msg_id):
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    msg = db.session.get(EmailMessage, msg_id)
    addr = msg.to_addr if msg else None
    if msg:
        db.session.delete(msg)
        db.session.commit()
    if addr and EmailMessage.query.filter_by(to_addr=addr).count():
        return redirect(url_for('inbox_address', addr=addr))
    return redirect(url_for('inbox'))

@app.route('/inbox/address/<path:addr>/delete', methods=['POST'])
def inbox_delete_address(addr):
    if not _is_admin():
        return jsonify({'error': 'Unauthorized'}), 403
    EmailMessage.query.filter_by(to_addr=addr.strip().lower()).delete()
    db.session.commit()
    return redirect(url_for('inbox'))

# ---------------------------- #

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5050)
