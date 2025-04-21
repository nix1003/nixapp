from flask import Flask, render_template, request, redirect, url_for, session
app = Flask(__name__)
app.secret_key = 'your_secret_key'
@app.route('/downloads')
def downloads():
    return render_template('downloads.html')

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/inventory')
def inventory():
    is_admin = session.get('admin', False)
    return render_template('inventory.html', is_admin=is_admin)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = request.form['username']
        password = request.form['password']
        if user == 'admin' and password == '1234':
            session['admin'] = True
            return redirect(url_for('inventory'))
        else:
            error = "Invalid credentials. Please try again."
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('admin', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True, port=5050)
