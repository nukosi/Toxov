from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, session as flask_session
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import datetime
import secrets
import logging
import stripe
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration

_sentry_dsn = os.environ.get("SENTRY_DSN", "")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,  # リクエストの10%でパフォーマンス計測
        send_default_pii=False,  # メアド等の個人情報は送らない
    )
from database import (init_db, load_config, save_config,
                      verify_password, create_user, get_user_by_id, get_user_by_token,
                      set_emergency_unblock, add_event_log, get_event_logs, get_streak,
                      set_user_plan, has_emergency_history, get_success_rate,
                      apply_event_points, get_user_points, get_season_ranking, get_rank,
                      get_user_by_connect_code, get_user_by_email, get_user_by_username, set_user_email,
                      create_reset_token, verify_reset_token, consume_reset_token, update_password,
                      add_app_to_config, record_first_save, record_first_sync, get_analytics,
                      set_stripe_subscription, get_user_by_stripe_customer_id,
                      get_streak_shield, use_streak_shield,
                      get_premium_count, get_user_rank_position,
                      set_otp, verify_otp,
                      create_todo_task, get_todo_tasks, delete_todo_task,
                      toggle_todo_completion, get_todo_completions_today, get_todo_stats)
from comments import get_comment, get_phase
from plans import get_limits, within_site_limit, within_app_limit, count_unique_domains
from mailer import send_password_reset, send_otp_email, send_contact_email
from translations import get_t

# 管理者専用Todoテンプレート（変数: deadline_timeは別フィールド、vars内で定義）
TODO_TEMPLATES = [
    {"key": "squat",    "category": "運動", "label": "スクワット",
     "pattern": "{deadline}までにスクワットを{count}回する",
     "vars": [{"name": "count", "type": "number", "label": "回数", "default": 10}]},
    {"key": "pushup",   "category": "運動", "label": "腕立て伏せ",
     "pattern": "{deadline}までに腕立て伏せを{count}回する",
     "vars": [{"name": "count", "type": "number", "label": "回数", "default": 10}]},
    {"key": "running",  "category": "運動", "label": "ランニング",
     "pattern": "{deadline}までにランニングを{duration}分する",
     "vars": [{"name": "duration", "type": "number", "label": "分数", "default": 30}]},
    {"key": "stretch",  "category": "運動", "label": "ストレッチ",
     "pattern": "{deadline}までにストレッチを{duration}分する",
     "vars": [{"name": "duration", "type": "number", "label": "分数", "default": 10}]},
    {"key": "book",     "category": "学習", "label": "参考書を読む",
     "pattern": "{deadline}までに「{title}」を{pages}ページ読む",
     "vars": [
         {"name": "title", "type": "text",   "label": "本の名前", "default": ""},
         {"name": "pages", "type": "number", "label": "ページ数", "default": 10},
     ]},
    {"key": "vocab",    "category": "学習", "label": "単語を覚える",
     "pattern": "{deadline}までに「{title}」の単語を{count}個覚える",
     "vars": [
         {"name": "title", "type": "text",   "label": "教材名", "default": ""},
         {"name": "count", "type": "number", "label": "個数",   "default": 20},
     ]},
    {"key": "problems", "category": "学習", "label": "問題集を解く",
     "pattern": "{deadline}までに「{title}」の問題を{count}問解く",
     "vars": [
         {"name": "title", "type": "text",   "label": "教材名", "default": ""},
         {"name": "count", "type": "number", "label": "問数",   "default": 10},
     ]},
    {"key": "study",    "category": "学習", "label": "勉強する",
     "pattern": "{deadline}までに勉強を{duration}分する",
     "vars": [{"name": "duration", "type": "number", "label": "分数", "default": 60}]},
]

_TODO_TEMPLATE_MAP = {t["key"]: t for t in TODO_TEMPLATES}


def render_todo_text(template_key: str, variables: dict, deadline_time: str) -> str:
    """テンプレートキーと変数からタスクの表示テキストを生成する。"""
    tmpl = _TODO_TEMPLATE_MAP.get(template_key)
    if not tmpl:
        return "不明なタスク"
    text = tmpl["pattern"].replace("{deadline}", deadline_time)
    for k, v in variables.items():
        text = text.replace("{" + k + "}", str(v))
    return text


PRESET_SITES = [
    {
        "category": "cat_video",
        "collapsed": False,
        "sites": [
            {"name": "YouTube",  "domains": ["youtube.com", "www.youtube.com", "youtu.be"]},
            {"name": "Netflix",  "domains": ["netflix.com"]},
            {"name": "Twitch",   "domains": ["twitch.tv"]},
            {"name": "ニコニコ", "domains": ["nicovideo.jp"]},
        ],
    },
    {
        "category": "cat_sns",
        "collapsed": False,
        "sites": [
            {"name": "X",          "domains": ["x.com", "twitter.com"]},
            {"name": "Instagram",  "domains": ["instagram.com"]},
            {"name": "TikTok",     "domains": ["tiktok.com"]},
            {"name": "Reddit",     "domains": ["reddit.com"]},
            {"name": "Facebook",   "domains": ["facebook.com"]},
            {"name": "Discord",    "domains": ["discord.com", "discordapp.com", "discord.gg"]},
        ],
    },
    {
        "category": "cat_adult",
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


def _get_lang():
    return flask_session.get("lang", "ja")


@app.context_processor
def inject_lang():
    lang = _get_lang()
    return {"t": get_t(lang), "lang": lang}

# エージェント認証シークレット。Railway環境変数 AGENT_SECRET と agent.py の定数を同じ値にする
AGENT_SECRET = os.environ.get("AGENT_SECRET", "")

# Stripe設定。Railway環境変数に設定してから使う
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_MONTHLY   = os.environ.get("STRIPE_PRICE_MONTHLY", "").strip()
STRIPE_PRICE_YEARLY    = os.environ.get("STRIPE_PRICE_YEARLY", "").strip()
STRIPE_PRICE_EARLYBIRD = os.environ.get("STRIPE_PRICE_EARLYBIRD", "").strip()
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
    stripe.timeout = 15

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


@app.route("/en")
def landing_en():
    flask_session["lang"] = "en"
    eb = _earlybird_status()
    return render_template("landing_en.html",
                           earlybird_available=eb["available"],
                           earlybird_remaining=eb["remaining"])


@app.route("/ja")
@app.route("/jp")
def landing_ja():
    flask_session["lang"] = "ja"
    return redirect(url_for("index"))


@app.route("/")
def index():
    # 未ログインはランディングページ（言語自動判定）、ログイン済みはダッシュボード
    if not current_user.is_authenticated:
        if "lang" not in flask_session:
            lang = request.accept_languages.best_match(["ja", "en"], default="ja")
            flask_session["lang"] = lang
        if flask_session.get("lang") == "en":
            return redirect(url_for("landing_en"))
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
    comment      = get_comment(phase, _get_lang())
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
    todo_tasks        = get_todo_tasks(current_user.id) if current_user.role == "admin" else []
    completions_today = get_todo_completions_today(current_user.id) if current_user.role == "admin" else set()
    todo_stats        = get_todo_stats(current_user.id) if current_user.role == "admin" else []
    today_weekday     = datetime.datetime.now(JST).weekday()
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
                           stripe_price_earlybird=STRIPE_PRICE_EARLYBIRD,
                           todo_tasks=todo_tasks, completions_today=completions_today,
                           todo_stats=todo_stats, todo_templates=TODO_TEMPLATES,
                           today_weekday=today_weekday, render_todo_text=render_todo_text,
                           tab=request.args.get("tab"))


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
        # 全プラン共通：追加のみ許可、既存サイト・アプリの削除は不可
        if not set(config["sites"]).issubset(set(sites)):
            return redirect(url_for("index", error="blocking"))
        if not set(config["apps"]).issubset(set(apps)):
            return redirect(url_for("index", error="blocking"))

    # プランの上限チェック（www.youtube.com と youtube.com は同一サービスとして1件扱い）
    if not within_site_limit(current_user.plan, count_unique_domains(sites)):
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
    config = load_config(user["id"])
    config["streak"] = get_streak(user["id"])
    # ポイント・ランク・プラン情報をモバイルアプリ向けに追加する
    full_user = get_user_by_id(user["id"])
    pts = get_user_points(user["id"])
    config["season_points"]   = pts["season_points"]
    config["lifetime_points"] = pts["lifetime_points"]
    config["rank"]            = get_rank(pts["season_points"])
    config["plan"]            = full_user.get("plan", "free")
    config["role"]            = full_user.get("role", "user")
    phase = get_phase(config["streak"], has_emergency_history(user["id"]))
    config["streak_comment"]  = get_comment(phase, "ja")
    return jsonify(config)


@app.route("/api/emergency/<token>", methods=["POST"])
@csrf.exempt
def api_emergency(token):
    """モバイルアプリから緊急解除を発動するエンドポイント。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    full_user = get_user_by_id(user["id"])
    # Premiumシールドがあれば消費してストリーク保護（既存ログAPIと同じロジック）
    if full_user.get("plan") == "premium" and use_streak_shield(user["id"]):
        add_event_log(user["id"], "shield_used")
        set_emergency_unblock(user["id"], True)
        return jsonify({"ok": True, "shield_used": True})
    add_event_log(user["id"], "emergency_unblock")
    apply_event_points(user["id"], "emergency_unblock")
    set_emergency_unblock(user["id"], True)
    return jsonify({"ok": True, "shield_used": False})


@app.route("/api/stats/<token>")
@csrf.exempt
def api_stats(token):
    """モバイルアプリ向け統計エンドポイント：ログ・ランキングを返す。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    ranking     = get_season_ranking(limit=20)
    rank_pos    = get_user_rank_position(user["id"])
    logs        = get_event_logs(user["id"], limit=30)
    return jsonify({
        "ranking":      ranking,
        "rank_position": rank_pos,
        "logs":         logs,
    })


@app.route("/api/admin/set-plan/<token>", methods=["POST"])
@csrf.exempt
def api_admin_set_plan(token):
    """管理者専用：モバイルからプランをfree/premiumに切り替える（デバッグ用）。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    full_user = get_user_by_id(user["id"])
    if full_user.get("role") != "admin":
        return jsonify({"error": "admin only"}), 403
    data = request.get_json(silent=True) or {}
    plan = data.get("plan", "")
    if plan not in ("free", "premium"):
        return jsonify({"error": "invalid plan"}), 400
    set_user_plan(user["id"], plan)
    return jsonify({"ok": True, "plan": plan})


@app.route("/api/mobile/login", methods=["POST"])
@csrf.exempt
@limiter.limit("10 per minute")
def api_mobile_login():
    """メールアドレス/ユーザー名とパスワードでモバイルログインし、config_urlを返す。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    data      = request.get_json(silent=True) or {}
    login_id  = data.get("email", "").strip()
    password  = data.get("password", "")
    if not login_id or not password:
        return jsonify({"error": "missing_fields"}), 400
    # メールアドレスでユーザーを検索してユーザー名を取得する
    user_by_email = get_user_by_email(login_id.lower())
    username = user_by_email["username"] if user_by_email else login_id
    user = verify_password(username, password)
    if not user:
        return jsonify({"error": "invalid_credentials"}), 401
    # verify_passwordはapi_tokenを返さないのでfull_userから取得する
    full_user = get_user_by_id(user["id"])
    config_url = f"https://{request.host}/api/config/{full_user['api_token']}"
    return jsonify({"config_url": config_url})


@app.route("/api/mobile/register", methods=["POST"])
@csrf.exempt
@limiter.limit("5 per minute")
def api_mobile_register():
    """モバイルから新規ユーザー登録してconfig_urlを返す。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    data     = request.get_json(silent=True) or {}
    username = data.get("username", "").strip()
    email    = data.get("email", "").strip().lower()
    password = data.get("password", "")
    if not username or not email or not password:
        return jsonify({"error": "missing_fields"}), 400
    if len(username) < 3 or len(username) > 30:
        return jsonify({"error": "invalid_username"}), 400
    if len(password) < 8:
        return jsonify({"error": "password_too_short"}), 400
    if get_user_by_username(username):
        return jsonify({"error": "username_taken"}), 409
    if get_user_by_email(email):
        return jsonify({"error": "email_taken"}), 409
    create_user(username, password, email)
    user      = verify_password(username, password)
    full_user = get_user_by_id(user["id"])
    config_url = f"https://{request.host}/api/config/{full_user['api_token']}"
    return jsonify({"config_url": config_url}), 201


@app.route("/api/mobile/plans/<token>")
@csrf.exempt
def api_mobile_plans(token):
    """利用可能な料金プランとStripe価格情報を返す。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    plan_defs = [
        ("monthly",   STRIPE_PRICE_MONTHLY,   "月額プラン"),
        ("yearly",    STRIPE_PRICE_YEARLY,     "年額プラン"),
        ("earlybird", STRIPE_PRICE_EARLYBIRD,  "アーリーバード年額"),
    ]
    plans = []
    for plan_id, price_id, label in plan_defs:
        if not price_id or not STRIPE_SECRET_KEY:
            continue
        try:
            price = stripe.Price.retrieve(price_id)
            plans.append({
                "id":       plan_id,
                "label":    label,
                "amount":   price.unit_amount,
                "currency": price.currency,
                "interval": getattr(price.recurring, "interval", None) if price.recurring else None,
            })
        except Exception:
            pass
    return jsonify({"plans": plans})


@app.route("/api/mobile/checkout/<token>")
@csrf.exempt
def api_mobile_checkout(token):
    """モバイルアプリ向けStripe Checkoutセッション生成エンドポイント。ログイン不要でStripe決済URLを返す。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "stripe_not_configured"}), 503
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    full_user = get_user_by_id(user["id"])
    if full_user.get("plan") == "premium":
        return jsonify({"error": "already_premium"}), 400
    # ?plan=monthly|yearly|earlybird で料金プランを選択する
    plan_name = request.args.get("plan", "monthly")
    price_map = {
        "monthly":   STRIPE_PRICE_MONTHLY,
        "yearly":    STRIPE_PRICE_YEARLY,
        "earlybird": STRIPE_PRICE_EARLYBIRD,
    }
    price_id = price_map.get(plan_name) or STRIPE_PRICE_MONTHLY
    if not price_id:
        return jsonify({"error": "stripe_not_configured"}), 503
    success_url = (
        url_for("billing_mobile_success", _external=True)
        + f"?session_id={{CHECKOUT_SESSION_ID}}&token={token}"
    )
    params = {
        "line_items": [{"price": price_id, "quantity": 1}],
        "mode": "subscription",
        "subscription_data": {"trial_period_days": 7},
        "client_reference_id": str(user["id"]),
        "success_url": success_url,
        "cancel_url": url_for("index", _external=True),
        "locale": "ja",
    }
    if full_user.get("stripe_customer_id"):
        params["customer"] = full_user["stripe_customer_id"]
    elif full_user.get("email"):
        params["customer_email"] = full_user["email"]
    try:
        session = stripe.checkout.Session.create(**params)
    except stripe.error.InvalidRequestError as e:
        if "similar object exists in test mode" in str(e):
            params.pop("customer", None)
            if full_user.get("email"):
                params["customer_email"] = full_user["email"]
            session = stripe.checkout.Session.create(**params)
        else:
            raise
    return jsonify({"url": session.url})


@app.route("/billing/mobile-success")
@csrf.exempt
def billing_mobile_success():
    """Stripe決済完了後のモバイル向けコールバック。ログイン不要でプランを即時反映する。"""
    session_id = request.args.get("session_id", "")
    if session_id and STRIPE_SECRET_KEY:
        try:
            sess = stripe.checkout.Session.retrieve(session_id)
            if getattr(sess, "payment_status", None) in ("paid", "no_payment_required"):
                user_id = int(getattr(sess, "client_reference_id", None) or 0)
                if user_id:
                    set_stripe_subscription(
                        user_id,
                        customer_id=getattr(sess, "customer", None),
                        subscription_id=getattr(sess, "subscription", None),
                        plan="premium",
                    )
        except Exception:
            pass
    return (
        "<html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<style>body{background:#0f0f0f;color:#fff;font-family:sans-serif;"
        "text-align:center;padding:60px 20px}h2{font-size:24px;margin-bottom:12px}"
        "p{color:#888;font-size:15px}</style></head>"
        "<body><h2>✅ Premiumへようこそ！</h2>"
        "<p>アプリを開いてご利用ください。<br>ホーム画面を下に引っ張って更新すると反映されます。</p>"
        "</body></html>"
    )


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


@app.route("/tokusho-en")
def tokusho_en():
    return render_template("tokusho_en.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/privacy-en")
def privacy_en():
    return render_template("privacy_en.html")


@app.route("/terms")
def terms():
    return render_template("terms.html")


@app.route("/terms-en")
def terms_en():
    return render_template("terms_en.html")


# スパム防止用: {ip: last_submit_timestamp}
_contact_rate: dict = {}

def _check_contact_rate(ip: str) -> bool:
    """60秒以内に同一IPから送信済みならFalseを返す。"""
    import time
    now = time.time()
    last = _contact_rate.get(ip, 0)
    if now - last < 60:
        return False
    _contact_rate[ip] = now
    return True


@app.route("/contact", methods=["GET", "POST"])
def contact():
    sent = error = None
    name = email = message = None
    if request.method == "POST":
        name    = request.form.get("name", "").strip()[:100]
        email   = request.form.get("email", "").strip()[:200]
        message = request.form.get("message", "").strip()[:3000]
        if not email or "@" not in email:
            error = "メールアドレスを正しく入力してください。"
        elif not message:
            error = "お問い合わせ内容を入力してください。"
        elif not _check_contact_rate(request.remote_addr):
            error = "送信が連続しています。しばらく待ってから再度お試しください。"
        else:
            send_contact_email(name, email, message, lang="ja")
            sent = True
    return render_template("contact.html", sent=sent, error=error,
                           name=name, email=email, message=message)


@app.route("/contact-en", methods=["GET", "POST"])
def contact_en():
    sent = error = None
    name = email = message = None
    if request.method == "POST":
        name    = request.form.get("name", "").strip()[:100]
        email   = request.form.get("email", "").strip()[:200]
        message = request.form.get("message", "").strip()[:3000]
        if not email or "@" not in email:
            error = "Please enter a valid email address."
        elif not message:
            error = "Please enter your message."
        elif not _check_contact_rate(request.remote_addr):
            error = "Too many requests. Please wait a moment and try again."
        else:
            send_contact_email(name, email, message, lang="en")
            sent = True
    return render_template("contact_en.html", sent=sent, error=error,
                           name=name, email=email, message=message)


@app.route("/billing/checkout", methods=["POST"])
@login_required
def billing_checkout():
    price_id    = request.form.get("price_id", "").strip()
    valid_prices = {STRIPE_PRICE_MONTHLY, STRIPE_PRICE_YEARLY, STRIPE_PRICE_EARLYBIRD} - {""}
    if price_id not in valid_prices or not STRIPE_SECRET_KEY:
        return redirect(url_for("index"))
    try:
        user_data = get_user_by_id(current_user.id)
        params = {
            "payment_method_types": ["card"],
            "line_items": [{"price": price_id, "quantity": 1}],
            "mode": "subscription",
            "subscription_data": {"trial_period_days": 7},
            "client_reference_id": str(current_user.id),
            "success_url": url_for("billing_success", _external=True) + "?session_id={CHECKOUT_SESSION_ID}",
            "cancel_url":  url_for("index", _external=True),
            "locale": "en" if _get_lang() == "en" else "ja",
        }
        if user_data.get("stripe_customer_id"):
            params["customer"] = user_data["stripe_customer_id"]
        elif user_data.get("email"):
            params["customer_email"] = user_data["email"]
        try:
            session = stripe.checkout.Session.create(**params)
        except stripe.error.InvalidRequestError as e:
            # テストモードの顧客IDが本番で使えない場合はIDを外して再試行
            if "similar object exists in test mode" in str(e):
                params.pop("customer", None)
                if user_data.get("email"):
                    params["customer_email"] = user_data["email"]
                session = stripe.checkout.Session.create(**params)
            else:
                raise
        return redirect(session.url, 303)
    except Exception as e:
        return redirect(url_for("index"))


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


@app.route("/api/sites/save/<token>", methods=["POST"])
@csrf.exempt
def api_sites_save(token):
    """モバイルアプリからサイトリストを更新するエンドポイント。ブロック時間中の制限はPC版 /save と同じルールを適用する。"""
    if not check_agent_secret():
        return jsonify({"error": "forbidden"}), 403
    user = get_user_by_token(token)
    if not user:
        return jsonify({"error": "invalid token"}), 401
    data  = request.get_json(silent=True) or {}
    sites = data.get("sites", [])
    if not isinstance(sites, list):
        return jsonify({"error": "invalid sites"}), 400
    # 空白・空文字を除去してサニタイズ
    sites = [str(s).strip() for s in sites if str(s).strip()]
    full_user = get_user_by_id(user["id"])
    config    = load_config(user["id"])
    # ブロック時間中は全プラン共通で追加のみ許可、削除は不可
    if is_blocking_time(config):
        if not set(config["sites"]).issubset(set(sites)):
            return jsonify({"error": "blocking"}), 403
    # プランの上限チェック（www.youtube.com と youtube.com は同一サービスとして1件扱い）
    if not within_site_limit(full_user.get("plan", "free"), count_unique_domains(sites)):
        return jsonify({"error": "site_limit"}), 403
    # block_start/block_end/appsはそのまま保持し、sitesのみ更新する
    save_config(user["id"], config["block_start"], config["block_end"], sites, config["apps"])
    return jsonify({"ok": True})


# ─── Todo（管理者専用） ────────────────────────────────────────────────────────

@app.route("/todo/create", methods=["POST"])
@login_required
def todo_create():
    if current_user.role != "admin":
        return redirect(url_for("index"))
    template_key  = request.form.get("template_key", "")
    deadline_time = request.form.get("deadline_time", "08:00")
    weekdays_raw  = request.form.getlist("weekdays")
    weekdays      = [int(d) for d in weekdays_raw if d.isdigit()]
    tmpl = _TODO_TEMPLATE_MAP.get(template_key)
    if not tmpl:
        return redirect(url_for("index", tab="todo"))
    variables = {}
    for var in tmpl["vars"]:
        val = request.form.get(f"var_{var['name']}", str(var["default"]))
        if var["type"] == "number":
            try:
                variables[var["name"]] = int(val)
            except ValueError:
                variables[var["name"]] = var["default"]
        else:
            variables[var["name"]] = val
    create_todo_task(current_user.id, template_key, variables, weekdays, deadline_time)
    return redirect(url_for("index", tab="todo"))


@app.route("/todo/complete/<int:task_id>", methods=["POST"])
@login_required
@csrf.exempt
def todo_complete(task_id):
    # セッション認証済み＋admin限定のAJAXエンドポイントのためCSRF免除
    if current_user.role != "admin":
        return jsonify({"error": "forbidden"}), 403
    done = toggle_todo_completion(current_user.id, task_id)
    return jsonify({"done": done})


@app.route("/todo/delete/<int:task_id>", methods=["POST"])
@login_required
def todo_delete(task_id):
    if current_user.role != "admin":
        return redirect(url_for("index"))
    delete_todo_task(current_user.id, task_id)
    return redirect(url_for("index", tab="todo"))


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
