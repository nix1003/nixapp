from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
import os

# Initialize Flask app
app = Flask(__name__, instance_relative_config=True)
app.secret_key = 'your_secret_key'
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

# Multi-admin credentials
ADMINS = {
    'lb': '6918',
    'modz': '6918'
}

# Database model
class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, default=0.0)
    url = db.Column(db.String(255))

# Routes
@app.route('/debug-db-info')
def debug_db_info():
    db_path_real = db_path
    exists = os.path.exists(db_path_real)
    return jsonify({
        'db_path': db_path_real,
        'exists': exists
    })

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
        if uploaded_file and uploaded_file.filename != '':
            filepath = os.path.join(upload_dir, uploaded_file.filename)
            uploaded_file.save(filepath)
            flash('File uploaded successfully!')

    files = os.listdir(upload_dir)
    return render_template('downloads.html', is_admin=is_admin, files=files)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_type') == 'admin':
        return redirect(url_for('inventory'))

    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']

        if user in ADMINS and ADMINS[user] == password:
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

@app.route('/sessiontest')
def session_test():
    return jsonify(dict(session))

# ---------------------------- #

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, port=5050)