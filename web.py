from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, session as flask_session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import datetime
import secrets
import stripe
from database import (init_db, load_config, save_config,
                      verify_password, create_user, get_user_by_id, get_user_by_token,
                      set_emergency_unblock, add_event_log, get_event_logs, get_streak,
                      set_user_plan, has_emergency_history, get_success_rate,
                      apply_event_points, get_user_points, get_season_ranking, get_rank,
                      get_user_by_connect_code, get_user_by_email, set_user_email,
                      create_reset_token, verify_reset_token, consume_reset_token, update_password,
                      add_app_to_config, record_first_save, record_first_sync, get_analytics,
                      set_stripe_subscription, get_user_by_stripe_customer_id,
                      get_streak_shield, use_streak_shield,
                      get_premium_count, get_user_rank_position,
                      set_otp, verify_otp)
from comments import get_comment, get_phase
from plans import get_limits, within_site_limit, within_app_limit
from mailer import send_password_reset, send_otp_email

PRESET_SITES = [
    {
        "category": "動画",
        "collapsed": False,
        "sites": [
            {"name": "YouTube",  "domains": ["youtube.com", "www.youtube.com", "youtu.be"]},
            {"name": "Netflix",  "domains": ["netflix.com"]},
            {"name": "Twitch",   "domains": ["twitch.tv"]},
            {"name": "ニコニコ", "domains": ["nicovideo.jp"]},
        ],
    },
    {
        "category": "SNS",
        "collapsed": False,
        "sites": [
            {"name": "X",          "domains": ["x.com", "twitter.com"]},
            {"name": "Instagram",  "domains": ["instagram.com"]},
            {"name": "TikTok",     "domains": ["tiktok.com"]},
            {"name": "Reddit",     "domains": ["reddit.com"]},
            {"name": "Facebook",   "domains": ["facebook.com"]},
            {"name": "Discord",    "domains": ["discord.com"]},
        ],
    },
    {
        "category": "成人向け",
        "collapsed": True,
        "sites": [
            {"name": "Pornhub", "domains": ["pornhub.com"]},
            {"name": "xVideos", "domains": ["xvideos.com"]},
            {"name": "xHamster","domains": ["xhamster.com"]},
        ],
    },
]

app = Flask(__name__)
# 本番環境では環境変数 SECRET_KEY に強いランダム文字列を設定すること
app.secret_key = os.environ.get("SECRET_KEY", "nukosisnsblocker-secret-key-change-this-later")

# Railway等のリバースプロキシ配下でX-Forwarded-Protoを正しく解釈させる
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# RailwayサーバーはHTTPS必須。RAILWAY_SERVICE_NAMEで本番環境を検出する
_IS_PRODUCTION = bool(os.environ.get("RAILWAY_SERVICE_NAME"))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"]   = _IS_PRODUCTION
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# CSRFトークンはSECURE接続でのみ送信（本番のみ）
app.config["WTF_CSRF_SSL_STRICT"] = _IS_PRODUCTION

login_manager = LoginManager(app)
login_manager.login_view = "login"

csrf    = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=[], storage_uri="memory://")

# エージェント認証シークレット。Railway環境変数 AGENT_SECRET と agent.py の定数を同じ値にする
AGENT_SECRET = os.environ.get("AGENT_SECRET", "")

# Stripe設定。Railway環境変数に設定してから使う
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY   = os.environ.get("STRIPE_PRICE_MONTHLY", "")
STRIPE_PRICE_YEARLY    = os.environ.get("STRIPE_PRICE_YEARLY", "")
STRIPE_PRICE_EARLYBIRD = os.environ.get("STRIPE_PRICE_EARLYBIRD", "")
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# RailwayサーバーはUTCなので、表示・判定にはJSTに変換して使う
JST = datetime.timezone(datetime.timedelta(hours=9))

# アーリーバード: 先着50名 または 6/30 23:59 JST まで（先に達した方が優先）
EARLYBIRD_MAX      = 50
EARLYBIRD_DEADLINE = datetime.datetime(2026, 6, 30, 23, 59, 0)  # tzinfo なし・JST 想定


def _earlybird_status() -> dict:
    """アーリーバードが購入可能か・残り枠数を返す。"""
    now_jst   = datetime.datetime.now(JST).replace(tzinfo=None)
    count     = get_premium_count()
    remaining = max(0, EARLYBIRD_MAX - count)
    available = remaining > 0 and now_jst <= EARLYBIRD_DEADLINE
    return {"available": available, "remaining": remaining}


# gunicornはif __name__ == "__main__"を通らないのでここで初期化する
init_db()


# CSRFトークン期限切れ時はフレンドリーなバナーにリダイレクト
@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return redirect(url_for("index", error="session_expired"))


def check_agent_secret() -> bool:
    """AGENT_SECRETが設定済みの場合、X-Toxov-Keyヘッダーを検証する。"""
    if not AGENT_SECRET:
        return True  # 未設定（ローカル開発）はスキップ
    return request.headers.get("X-Toxov-Key") == AGENT_SECRET


@app.before_request
def force_https():
    # ProxyFix適用後、request.is_secureはX-Forwarded-Protoを正しく反映する
    # localhostはHTTPのまま許容する
    if (not request.is_secure
            and not request.host.startswith(("localhost", "127.0.0.1"))):
        return redirect(request.url.replace("http://", "https://", 1), 301)


class User(UserMixin):
    def __init__(self, id, username, api_token, plan="free", role="user", connect_code=None):
        self.id           = id
        self.username     = username
        self.api_token    = api_token
        self.plan         = plan
        self.role         = role
        self.connect_code = connect_code


@login_manager.user_loader
def load_user(user_id):
    data = get_user_by_id(int(user_id))
    return User(data["id"], data["username"], data["api_token"],
                data.get("plan", "free"), data.get("role", "user"),
                data.get("connect_code")) if data else None


def streak_emoji(days: int) -> str:
    if days >= 30:
        return '💎'
    if days >= 3:
        return '🔥'
    return '🌱'


def is_blocking_time(config):
    now = datetime.datetime.now(JST).time()
    sh, sm = map(int, config["block_start"].split(":"))
    eh, em = map(int, config["block_end"].split(":"))
    return datetime.time(sh, sm) <= now < datetime.time(eh, em)


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()[:50]
        password = request.form["password"].strip()[:200]
        email    = request.form.get("email", "").strip()[:200] or None
        if username and password:
            from database import Session, engine, UserModel
            with Session(engine) as session:
                exists = session.query(UserModel).filter_by(username=username).first()
            if exists:
                error = "そのユーザー名はすでに使われています"
            elif email and get_user_by_email(email):
                error = "そのメールアドレスはすでに使われています"
            else:
                create_user(username, password, email)
                return redirect(url_for("login"))
        else:
            error = "ユーザー名とパスワードを入力してください"
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    error = None
    if request.method == "POST":
        username = request.form["username"].strip()[:50]
        password = request.form["password"].strip()[:200]
        user = verify_password(username, password)
        if user:
            data = get_user_by_id(user["id"])
            if data.get("role") == "admin":
                # 管理者は2FA必須：OTPを生成してメール送信し確認画面へ
                if not data.get("email"):
                    error = "管理者アカウントにメールアドレスが登録されていません"
                else:
                    code       = f"{secrets.randbelow(1000000):06d}"
                    expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=10)
                    set_otp(data["id"], code, expires_at)
                    send_otp_email(data["email"], code)
                    flask_session["_2fa_user_id"] = data["id"]
                    return redirect(url_for("login_verify"))
            else:
                login_user(User(data["id"], data["username"], data["api_token"],
                               data.get("plan", "free"), data.get("role", "user")))
                return redirect(url_for("index"))
        if not error:
            error = "ユーザー名またはパスワードが違います"
    return render_template("login.html", error=error)


@app.route("/login/verify", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login_verify():
    user_id = flask_session.get("_2fa_user_id")
    if not user_id:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        if verify_otp(user_id, code):
            flask_session.pop("_2fa_user_id", None)
            data = get_user_by_id(user_id)
            login_user(User(data["id"], data["username"], data["api_token"],
                           data.get("plan", "free"), data.get("role", "user"),
                           data.get("connect_code")))
            return redirect(url_for("index"))
        error = "コードが正しくないか期限切れです"
    return render_template("login_verify.html", error=error)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/")
def index():
    # 未ログインはランディングページ、ログイン済みはダッシュボードを返す
    if not current_user.is_authenticated:
        eb = _earlybird_status()
        return render_template("landing.html",
                               earlybird_available=eb["available"],
                               earlybird_remaining=eb["remaining"])
    config   = load_config(current_user.id)
    blocking = is_blocking_time(config)
    saved    = request.args.get("saved", False)
    error    = request.args.get("error")
    logs     = get_event_logs(current_user.id)
    streak       = get_streak(current_user.id)
    semoji       = streak_emoji(streak)
    phase        = get_phase(streak, has_emergency_history(current_user.id))
    comment      = get_comment(phase)
    weekly_rate  = get_success_rate(current_user.id, 7)
    monthly_rate = get_success_rate(current_user.id, 30)
    points       = get_user_points(current_user.id)
    rank         = get_rank(points["season_points"])
    ranking      = get_season_ranking()
    for r in ranking:
        r["rank"] = get_rank(r["season_points"])
    limits        = get_limits(current_user.plan)
    streak_shield = get_streak_shield(current_user.id)
    user_rank     = get_user_rank_position(current_user.id)
    eb            = _earlybird_status()
    analytics = get_analytics() if current_user.role == "admin" else None
    # エージェントが直近90秒以内にpollしていれば接続中とみなす（poll間隔30秒の3倍）
    user_data       = get_user_by_id(current_user.id)
    last_active_at  = user_data.get("last_active_at")
    now_jst         = datetime.datetime.now(JST).replace(tzinfo=None)
    agent_connected = (
        last_active_at is not None and
        (now_jst - last_active_at).total_seconds() < 90
    )
    return render_template("index.html", config=config, blocking=blocking,
                           saved=saved, api_token=current_user.api_token,
                           connect_code=current_user.connect_code,
                           logs=logs, streak=streak, semoji=semoji, comment=comment,
                           weekly_rate=weekly_rate, monthly_rate=monthly_rate,
                           points=points, rank=rank, ranking=ranking, error=error,
                           plan=current_user.plan, limits=limits,
                           streak_shield=streak_shield,
                           user_rank=user_rank,
                           earlybird_available=eb["available"],
                           earlybird_remaining=eb["remaining"],
                           role=current_user.role, presets=PRESET_SITES,
                           analytics=analytics, agent_connected=agent_connected,
                           payment=request.args.get("payment"),
                           plan_expires_at=user_data.get("plan_expires_at"),
                           stripe_price_monthly=STRIPE_PRICE_MONTHLY,
                           stripe_price_yearly=STRIPE_PRICE_YEARLY,
                           stripe_price_earlybird=STRIPE_PRICE_EARLYBIRD)


@app.route("/save", methods=["POST"])
@login_required
def save():
    block_start = request.form["block_start"]
    block_end   = request.form["block_end"]
    sites_raw   = request.form.get("sites", "")
    apps_raw    = request.form.get("apps", "")
    sites       = [s.strip() for s in sites_raw.splitlines() if s.strip()]
    apps        = [a.strip() for a in apps_raw.splitlines() if a.strip()]

    config   = load_config(current_user.id)
    blocking = is_blocking_time(config)

    if blocking:
        # ブロック時間中はスケジュール変更不可（全プラン共通）
        if block_start != config["block_start"] or block_end != config["block_end"]:
            return redirect(url_for("index", error="blocking"))
        limits = get_limits(current_user.plan)
        if limits['block_time_add']:
            # Premium: 追加のみ許可（既存サイト・アプリの削除は不可）
            if not set(config["sites"]).issubset(set(sites)):
                return redirect(url_for("index", error="blocking"))
            if not set(config["apps"]).issubset(set(apps)):
                return redirect(url_for("index", error="blocking"))
        else:
            # 無料: 追加も削除も不可（完全ロック）
            if set(sites) != set(config["sites"]):
                return redirect(url_for("index", error="blocking_free"))
            if set(apps) != set(config["apps"]):
                return redirect(url_for("index", error="blocking_free"))

    # プランの上限チェック
    if not within_site_limit(current_user.plan, len(sites)):
        return redirect(url_for("index", error="site_limit"))
    if not within_app_limit(current_user.plan, len(apps)):
        return redirect(url_for("index", error="app_limit"))

    save_config(current_user.id, block_start, block_end, sites, apps)
    record_first_save(current_user.id)
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


@app.route("/admin/set-plan", methods=["POST"])
@login_required
def admin_set_plan():
    # admin ロール以外は何もせずトップへ
    if current_user.role != "admin":
        return redirect(url_for("index"))
    plan = request.form.get("plan")
    if plan in ("free", "premium"):
        set_user_plan(current_user.id, plan)
    return redirect(url_for("index"))


@app.route("/api/connect/<code>")
@csrf.exempt
def api_connect(code):
    # 短いコードからユーザーのconfig URLを返す（Windowsエージェントの初回セットアップ用）
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_connect_code(code)
    if not user:
        return jsonify({"error": "invalid code"}), 404
    config_url = f"https://{request.host}/api/config/{user['api_token']}"
    return jsonify({"config_url": config_url})


@app.route("/api/config/<token>")
@csrf.exempt
def api_config(token):
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    record_first_sync(user["id"])
    # streakをconfig APIに含めることでモバイルアプリからも参照できるようにする
    config = load_config(user["id"])
    config["streak"] = get_streak(user["id"])
    return jsonify(config)


@app.route("/api/apps/add/<token>", methods=["POST"])
@csrf.exempt
def api_apps_add(token):
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    path = (request.json or {}).get("path", "").strip()
    # .exe 以外は拒否する（パストラバーサル等を防ぐ簡易チェック）
    if not path.lower().endswith(".exe"):
        return jsonify({"error": "invalid path"}), 400
    # プランの上限チェック
    full_user = get_user_by_id(user["id"])
    config    = load_config(user["id"])
    if not within_app_limit(full_user["plan"], len(config["apps"]) + 1):
        return jsonify({"error": "app_limit"}), 403
    add_app_to_config(user["id"], path)
    return jsonify({"ok": True})


@app.route("/api/log/<token>", methods=["POST"])
@csrf.exempt
def api_log(token):
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    # request.jsonがNoneの場合（Content-Type未設定等）は空dictにフォールバック
    body  = request.json or {}
    event = body.get("event")
    # 想定外の値がDBに入らないよう許可リストで絞る
    if event in ("block_start", "block_end", "emergency_unblock"):
        # emergency_unblockはPremiumシールドがあれば消費してストリークを保護する
        if event == "emergency_unblock":
            full_user = get_user_by_id(user["id"])
            if full_user.get("plan") == "premium" and use_streak_shield(user["id"]):
                # シールド消費をログに残す（緊急解除は記録せずストリーク保護）
                add_event_log(user["id"], "shield_used")
                return jsonify({"ok": True, "shield_used": True})
        add_event_log(user["id"], event)
        apply_event_points(user["id"], event)
    return jsonify({"ok": True})


@app.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip()[:200]
        user = get_user_by_email(email) if email else None
        # セキュリティのため、メールが存在しない場合も「送信した」と表示する
        if user:
            token     = create_reset_token(user["id"])
            reset_url = url_for("reset_password", token=token, _external=True)
            send_password_reset(email, reset_url)
        return render_template("forgot_password.html", sent=True)
    return render_template("forgot_password.html", sent=False)


@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    # リンクを開いた時点でトークンの有効性を確認する
    valid = verify_reset_token(token)
    if not valid:
        return render_template("reset_password.html", error="このリンクは無効か期限切れです", token=token, expired=True)
    if request.method == "POST":
        password = request.form.get("password", "").strip()
        if len(password) < 6:
            return render_template("reset_password.html", error="パスワードは6文字以上で入力してください", token=token, expired=False)
        # POSTの直前に再度確認してからトークンを消費する（二重送信対策）
        user_id = consume_reset_token(token)
        if not user_id:
            return render_template("reset_password.html", error="このリンクは無効か期限切れです", token=token, expired=True)
        update_password(user_id, password)
        return redirect(url_for("login"))
    return render_template("reset_password.html", error=None, token=token, expired=False)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    message = None
    error   = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()[:200]
        if not email:
            error = "メールアドレスを入力してください"
        elif "@" not in email or "." not in email.split("@")[-1]:
            error = "有効なメールアドレスを入力してください"
        else:
            existing = get_user_by_email(email)
            if existing and existing["id"] != current_user.id:
                error = "そのメールアドレスはすでに使われています"
            else:
                set_user_email(current_user.id, email)
                message = "メールアドレスを保存しました"
    data = get_user_by_id(current_user.id)
    return render_template("profile.html", email=data.get("email"), message=message, error=error)


@app.route("/download")
def download():
    return send_from_directory("dist", "Toxov.exe", as_attachment=True)


@app.route("/tokusho")
def tokusho():
    return render_template("tokusho.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    price_id    = request.form.get("price_id", "").strip()
    valid_prices = {STRIPE_PRICE_MONTHLY, STRIPE_PRICE_YEARLY, STRIPE_PRICE_EARLYBIRD} - {""}
    if price_id not in valid_prices or not STRIPE_SECRET_KEY:
        return redirect(url_for("index"))
    user_data = get_user_by_id(current_user.id)
    params = {
        "payment_method_types": ["card"],
        "line_items": [{"price": price_id, "quantity": 1}],
        "mode": "subscription",
        # 7日間の無料トライアルをすべてのプランに付与する
        "subscription_data": {"trial_period_days": 7},
        # WebhookでユーザーIDを特定するためにclient_reference_idを使う
        "client_reference_id": str(current_user.id),
        "success_url": url_for("billing_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url":  url_for("index", _external=True),
    }
    # 既存のStripeカスタマーIDがあれば再利用する（重複カスタマー防止）
    if user_data.get("stripe_customer_id"):
        params["customer"] = user_data["stripe_customer_id"]
    elif user_data.get("email"):
        params["customer_email"] = user_data["email"]
    session = stripe.checkout.Session.create(**params)
    return redirect(session.url, 303)


@app.route("/billing/success")
@login_required
def billing_success():
    # StripeのCheckout SessionをAPIで直接取得してプランを即時反映する（Webhookの遅延・失敗への対策）
    session_id = request.args.get("session_id")
    if session_id and STRIPE_SECRET_KEY:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            status  = getattr(session, "payment_status", None)
            if status in ("paid", "no_payment_required"):
                user_id = int(getattr(session, "client_reference_id", None) or 0)
                if user_id == current_user.id:
                    set_stripe_subscription(
                        user_id,
                        customer_id     = getattr(session, "customer", None),
                        subscription_id = getattr(session, "subscription", None),
                        plan            = "premium",
                    )
        except Exception:
            pass
    return redirect(url_for("index", payment="success"))


@app.route("/billing/portal")
@login_required
def billing_portal():
    user_data   = get_user_by_id(current_user.id)
    customer_id = user_data.get("stripe_customer_id")
    if not customer_id or not STRIPE_SECRET_KEY:
        return redirect(url_for("index"))
    portal = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=url_for("index", _external=True),
    )
    return redirect(portal.url, 303)


@app.route("/billing/webhook", methods=["POST"])
@csrf.exempt
def billing_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get("Stripe-Signature", "")

    # 署名検証：失敗したら400を返してStripeに再送させない
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError:
        return jsonify({"error": "invalid payload"}), 400
    except Exception:
        return jsonify({"error": "invalid signature"}), 400

    # イベント処理：例外が起きても200を返してStripeのリトライを防ぐ
    # 新しいStripe SDKはStripeObjectがdictを継承しないため、属性アクセスで取得する
    try:
        etype = event.type
        obj   = event.data.object

        if etype == "checkout.session.completed":
            # サブスク開始（または無料トライアル開始）
            user_id = int(getattr(obj, "client_reference_id", None) or 0)
            if user_id:
                set_stripe_subscription(
                    user_id,
                    customer_id     = getattr(obj, "customer", None),
                    subscription_id = getattr(obj, "subscription", None),
                    plan            = "premium",
                )

        elif etype == "customer.subscription.updated":
            # サブスク更新・ステータス変化（active/trialing→premium、それ以外→free）
            customer_id = getattr(obj, "customer", "")
            user = get_user_by_stripe_customer_id(customer_id)
            if user:
                status     = getattr(obj, "status", "")
                period_end = getattr(obj, "current_period_end", None)
                expires_at = (datetime.datetime.utcfromtimestamp(period_end)
                              if period_end else None)
                new_plan   = "premium" if status in ("active", "trialing") else "free"
                set_stripe_subscription(
                    user["id"],
                    customer_id     = customer_id,
                    subscription_id = getattr(obj, "id", None),
                    plan            = new_plan,
                    plan_expires_at = expires_at,
                )

        elif etype == "customer.subscription.deleted":
            # 解約完了（猶予期間終了）→ freeに降格
            customer_id = getattr(obj, "customer", "")
            user = get_user_by_stripe_customer_id(customer_id)
            if user:
                set_stripe_subscription(
                    user["id"],
                    customer_id     = customer_id,
                    subscription_id = None,
                    plan            = "free",
                )

    except Exception as e:
        app.logger.error(f"[webhook] handler error: {e}")

    return jsonify({"ok": True})


@app.route("/api/me/subscription/<token>")
@csrf.exempt
def api_subscription(token):
    """エージェント・モバイル向けのサブスクリプション状態API。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    data       = get_user_by_id(user["id"])
    expires_at = data.get("plan_expires_at")
    return jsonify({
        "plan":           data.get("plan", "free"),
        "plan_expires_at": expires_at.isoformat() if expires_at else None,
    })


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
