from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
import os

# Initialize Flask app
app = Flask(__name__, instance_relative_config=True)
# Session secret comes from the SECRET_KEY environment variable (set it on Render).
# Locally, a random key is used if none is set (you'll just get logged out on restart).
_secret = os.environ.get("SECRET_KEY")
if not _secret:
    if os.environ.get("RENDER"):
        raise RuntimeError("SECRET_KEY environment variable is not set on Render")
    _secret = os.urandom(32).hex()
app.secret_key = _secret
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Smart database path switching
if os.environ.get("RENDER"):
    db_path = "/mnt/data/inventory.db"
    os.makedirs("/mnt/data", exist_ok=True)
else:
    db_path = os.path.join(app.instance_path, "inventory.db")
    os.makedirs(app.instance_path, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"
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

# Database model
class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, default=0.0)
    url = db.Column(db.String(255))

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


# ---------------------------- #

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5050)
