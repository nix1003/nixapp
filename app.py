from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

app.config['SESSION_COOKIE_DOMAIN'] = 'nixapp.org'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

INVENTORY_FILE = 'inventory.json'

ADMINS = {
    'lb': '6918$',
    'nic': '6918$'
}

def load_inventory():
    if not os.path.exists(INVENTORY_FILE):
        return []
    with open(INVENTORY_FILE, 'r') as f:
        return json.load(f)

def save_inventory(data):
    with open(INVENTORY_FILE, 'w') as f:
        json.dump(data, f, indent=2)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/inventory')
def inventory():
    is_admin = session.get('user_type') == 'admin'
    return render_template('inventory.html', is_admin=is_admin)

@app.route('/api/inventory')
def api_inventory():
    return jsonify(load_inventory())

@app.route('/api/update', methods=['POST'])
def update_item():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    inventory = load_inventory()

    for item in inventory:
        if item['id'] == data['id']:
            item['name'] = data['name']
            item['quantity'] = data['quantity']
            item['price'] = data['price']
            item['url'] = data['url']
            break

    save_inventory(inventory)
    return jsonify({'status': 'success'})

@app.route('/downloads')
def downloads():
    return render_template('downloads.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_type') == 'admin':
        print(f"Logged in as: {user}")
        print(f"Session data: {dict(session)}")
        return redirect(url_for('inventory'))

    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']

        # ✅ Check against admin list
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
    inventory = load_inventory()
    new_id = max([item.get("id", 0) for item in inventory], default=0) + 1

    new_item = {
        "id": new_id,
        "name": data.get("name"),
        "quantity": data.get("quantity"),
        "price": data.get("price", 0),
        "url": data.get("url", "")
    }

    inventory.append(new_item)
    save_inventory(inventory)
    return jsonify({'status': 'success'})

@app.route('/api/delete', methods=['POST'])
def api_delete_item():
    if session.get('user_type') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json()
    item_id = data.get("id")

    inventory = load_inventory()
    updated_inventory = [item for item in inventory if item["id"] != item_id]
    save_inventory(updated_inventory)

    return jsonify({'status': 'success'})

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
