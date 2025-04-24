from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_sqlalchemy import SQLAlchemy
import json
import os

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////mnt/data/inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
app.secret_key = 'your_secret_key'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Only use this in production
app.config['SESSION_COOKIE_DOMAIN'] = 'nixapp.org'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

INVENTORY_FILE = 'inventory.json'

# ✅ Multi-admin credentials
ADMINS = {
    'lb': '6918$',
    'nic': '6918$'
}

# ---------------------------- #
# Inventory file helpers
# ---------------------------- #



class InventoryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, default=0.0)
    url = db.Column(db.String(255))

# ---------------------------- #
# Routes
# ---------------------------- #

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
    return jsonify([{
        'id': item.id,
        'name': item.name,
        'quantity': item.quantity,
        'price': item.price,
        'url': item.url
    } for item in items])

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

@app.route('/downloads')
def downloads():
    return render_template('downloads.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Debug: log current session state
    print(f"Session BEFORE login: {dict(session)}")

    if session.get('user_type') == 'admin':
        print(f"Already logged in as: {session.get('username')}")
        return redirect(url_for('inventory'))

    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']
        print(f"Login attempt - user: {user}, password: {password}")

        if user in ADMINS and ADMINS[user] == password:
            session['user_type'] = 'admin'
            session['username'] = user
            session.permanent = True
            print(f"✅ Login success for user: {user}")
            print(f"Session AFTER login: {dict(session)}")
            return redirect(url_for('inventory'))
        else:
            print(f"❌ Login failed for user: {user}")
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
    print(f"Logging out user: {session.get('username')}")
    session.clear()
    return redirect(url_for('home'))

# ✅ Optional debug route
@app.route('/sessiontest')
def session_test():
    return jsonify(dict(session))

# ---------------------------- #

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, port=5050)
