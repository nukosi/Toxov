from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import os
import subprocess
import sys
import datetime
from database import init_db, load_config, save_config, verify_password, create_user, get_user_by_id as _get_user

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nukosisnsblocker-secret-key-change-this-later")

login_manager = LoginManager(app)
login_manager.login_view = "login"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLOCKER_FILE = os.path.join(BASE_DIR, "blocker.py")


class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username


@login_manager.user_loader
def load_user(user_id):
    from database import get_user_by_id
    data = get_user_by_id(int(user_id))
    return User(data["id"], data["username"]) if data else None


def is_blocking_time(config):
    now = datetime.datetime.now().time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = verify_password(request.form["username"], request.form["password"])
        if user:
            login_user(User(user["id"], user["username"]))
            return redirect(url_for("index"))
        error = "ユーザー名またはパスワードが違います"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    config = load_config()
    blocking = is_blocking_time(config)
    saved = request.args.get("saved", False)
    return render_template("index.html", config=config, blocking=blocking, saved=saved)


@app.route("/save", methods=["POST"])
@login_required
def save():
    block_start = request.form["block_start"]
    block_end = request.form["block_end"]
    sites_raw = request.form.get("sites", "")
    sites = [s.strip() for s in sites_raw.splitlines() if s.strip()]
    save_config(block_start, block_end, sites)
    return redirect(url_for("index", saved=1))


@app.route("/block", methods=["POST"])
@login_required
def block():
    subprocess.run([sys.executable, BLOCKER_FILE, "block"])
    return redirect(url_for("index"))


@app.route("/unblock", methods=["POST"])
@login_required
def unblock():
    subprocess.run([sys.executable, BLOCKER_FILE, "unblock"])
    return redirect(url_for("index"))


@app.route("/setup", methods=["GET", "POST"])
def setup():
    from database import Session, engine, UserModel
    with Session(engine) as session:
        user_exists = session.query(UserModel).first() is not None
    if user_exists:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if username and password:
            create_user(username, password)
            return redirect(url_for("login"))
        error = "ユーザー名とパスワードを入力してください"
    return render_template("setup.html", error=error)


@app.route("/api/config")
def api_config():
    return jsonify(load_config())


if __name__ == "__main__":
    init_db()
    print("nukosisnsblocker Web UI 起動中...")
    print("ブラウザで http://localhost:5000 を開いてください")
    app.run(host="0.0.0.0", port=5000, debug=False)
