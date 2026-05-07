from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import os
import datetime
from database import (init_db, load_config, save_config,
                      verify_password, create_user, get_user_by_id, get_user_by_token,
                      set_emergency_unblock, add_event_log, get_event_logs, get_streak)

app = Flask(__name__)
# 本番環境では環境変数 SECRET_KEY に強いランダム文字列を設定すること
app.secret_key = os.environ.get("SECRET_KEY", "nukosisnsblocker-secret-key-change-this-later")

login_manager = LoginManager(app)
login_manager.login_view = "login"

# RailwayサーバーはUTCなので、表示・判定にはJSTに変換して使う
JST = datetime.timezone(datetime.timedelta(hours=9))

# gunicornはif __name__ == "__main__"を通らないのでここで初期化する
init_db()


class User(UserMixin):
    def __init__(self, id, username, api_token):
        self.id        = id
        self.username  = username
        self.api_token = api_token


@login_manager.user_loader
def load_user(user_id):
    data = get_user_by_id(int(user_id))
    return User(data["id"], data["username"], data["api_token"]) if data else None


def is_blocking_time(config):
    now = datetime.datetime.now(JST).time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()
        if username and password:
            from database import Session, engine, UserModel
            with Session(engine) as session:
                exists = session.query(UserModel).filter_by(username=username).first()
            if exists:
                error = "そのユーザー名はすでに使われています"
            else:
                create_user(username, password)
                return redirect(url_for("login"))
        else:
            error = "ユーザー名とパスワードを入力してください"
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = verify_password(request.form["username"], request.form["password"])
        if user:
            data = get_user_by_id(user["id"])
            login_user(User(data["id"], data["username"], data["api_token"]))
            return redirect(url_for("index"))
        error = "ユーザー名またはパスワードが違います"
    return render_template("login.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
def index():
    # 未ログインはランディングページ、ログイン済みはダッシュボードを返す
    if not current_user.is_authenticated:
        return render_template("landing.html")
    config   = load_config(current_user.id)
    blocking = is_blocking_time(config)
    saved    = request.args.get("saved", False)
    logs     = get_event_logs(current_user.id)
    streak   = get_streak(current_user.id)
    return render_template("index.html", config=config, blocking=blocking,
                           saved=saved, api_token=current_user.api_token,
                           logs=logs, streak=streak)


@app.route("/save", methods=["POST"])
@login_required
def save():
    block_start = request.form["block_start"]
    block_end   = request.form["block_end"]
    sites_raw   = request.form.get("sites", "")
    apps_raw    = request.form.get("apps", "")
    sites       = [s.strip() for s in sites_raw.splitlines() if s.strip()]
    apps        = [a.strip() for a in apps_raw.splitlines() if a.strip()]
    save_config(current_user.id, block_start, block_end, sites, apps)
    return redirect(url_for("index", saved=1))


@app.route("/emergency", methods=["POST"])
@login_required
def emergency():
    set_emergency_unblock(current_user.id, True)
    return redirect(url_for("index"))


@app.route("/resume", methods=["POST"])
@login_required
def resume():
    set_emergency_unblock(current_user.id, False)
    return redirect(url_for("index"))


@app.route("/api/config/<token>")
def api_config(token):
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    # streakをconfig APIに含めることでモバイルアプリからも参照できるようにする
    config = load_config(user["id"])
    config["streak"] = get_streak(user["id"])
    return jsonify(config)


@app.route("/api/log/<token>", methods=["POST"])
def api_log(token):
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    event = request.json.get("event")
    # 想定外の値がDBに入らないよう許可リストで絞る
    if event in ("block_start", "block_end", "emergency_unblock"):
        add_event_log(user["id"], event)
    return jsonify({"ok": True})


@app.route("/download")
def download():
    return send_from_directory("dist", "Toxov.exe", as_attachment=True)


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


if __name__ == "__main__":
    print("Toxov Web UI 起動中...")
    print("ブラウザで http://localhost:5000 を開いてください")
    app.run(host="0.0.0.0", port=5000, debug=False)
