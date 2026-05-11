import os
import json
import secrets
import string
from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, DateTime
import datetime
from sqlalchemy.orm import declarative_base, Session, relationship
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

# 環境変数DATABASE_URLがあればPostgreSQL（Railway）、なければローカルのSQLiteを使う
_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'nukosisnsblocker.db')}")
# 古いRailwayは "postgres://" を返すがSQLAlchemyは "postgresql://" を要求するため変換する
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url)
Base = declarative_base()


class UserModel(Base):
    __tablename__ = "users"
    id        = Column(Integer, primary_key=True, autoincrement=True)
    username  = Column(String, nullable=False, unique=True)
    password  = Column(String, nullable=False)
    api_token = Column(String, nullable=False, unique=True)
    plan         = Column(String, nullable=False, default="free")
    role         = Column(String, nullable=False, default="user")
    connect_code = Column(String, nullable=True, unique=True)
    # パスワードリセット用。登録時は任意
    email        = Column(String, nullable=True, unique=True)
    # ユーザー動向記録
    registered_at  = Column(DateTime, nullable=True)
    first_save_at  = Column(DateTime, nullable=True)
    first_sync_at  = Column(DateTime, nullable=True)
    last_active_at = Column(DateTime, nullable=True)
    # Stripe サブスクリプション管理
    stripe_customer_id     = Column(String, nullable=True, unique=True)
    stripe_subscription_id = Column(String, nullable=True)
    trial_ends_at          = Column(DateTime, nullable=True)
    plan_expires_at        = Column(DateTime, nullable=True)
    # ストリーク保護シールド（Premium月次配布・持越しなし）
    streak_shield       = Column(Integer, nullable=True, default=0)
    shield_refreshed_at = Column(DateTime, nullable=True)
    config    = relationship("Config", back_populates="user", uselist=False, cascade="all, delete-orphan")
    sites     = relationship("Site", back_populates="user", cascade="all, delete-orphan")
    apps      = relationship("App",  back_populates="user", cascade="all, delete-orphan")


class Config(Base):
    __tablename__ = "config"
    id                = Column(Integer, primary_key=True, autoincrement=True)
    user_id           = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    block_start       = Column(String, nullable=False, default="08:00")
    block_end         = Column(String, nullable=False, default="21:00")
    emergency_unblock = Column(Boolean, nullable=False, default=False)
    version           = Column(Integer, nullable=False, default=0)
    user              = relationship("UserModel", back_populates="config")


class Site(Base):
    __tablename__ = "sites"
    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    domain  = Column(String, nullable=False)
    user    = relationship("UserModel", back_populates="sites")


class App(Base):
    __tablename__ = "apps"
    id      = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    path    = Column(String, nullable=False)
    user    = relationship("UserModel", back_populates="apps")


class EventLog(Base):
    __tablename__ = "event_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    event      = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class Season(Base):
    __tablename__ = "seasons"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    year       = Column(Integer, nullable=False)
    month      = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False)


class PointLog(Base):
    __tablename__ = "point_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    amount     = Column(Integer, nullable=False)
    reason     = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


class UserPoints(Base):
    __tablename__ = "user_points"
    user_id         = Column(Integer, ForeignKey("users.id"), primary_key=True)
    season_points   = Column(Integer, nullable=False, default=0)
    lifetime_points = Column(Integer, nullable=False, default=0)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    token      = Column(String, nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    # 使用済みフラグ。消去する代わりにマークするだけで履歴を保持する
    used       = Column(Boolean, nullable=False, default=False)


def init_db():
    Base.metadata.create_all(engine)
    _migrate()
    check_season_reset()


def _migrate():
    # 既存のDBを壊さずにカラムを追加するためのスキーママイグレーション
    # 新しいカラムを追加したときはここに ALTER TABLE を追記する
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        inspector = inspect(engine)
        # users テーブルに api_token がなければ追加
        user_columns = [c["name"] for c in inspector.get_columns("users")]
        if "api_token" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN api_token TEXT"))
            users = conn.execute(text("SELECT id FROM users")).fetchall()
            for (uid,) in users:
                token = secrets.token_urlsafe(32)
                conn.execute(text("UPDATE users SET api_token=:t WHERE id=:id"),
                             {"t": token, "id": uid})
            conn.commit()
        # users テーブルに email がなければ追加（パスワードリセット用、任意）
        if "email" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN email TEXT"))
            conn.commit()
        # users テーブルに plan がなければ追加（デフォルト free）
        if "plan" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'"))
            conn.commit()
        # users テーブルに role がなければ追加（デフォルト user）
        if "role" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'"))
            # connect_code がなければ追加して既存ユーザーにも発行する
        if "connect_code" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN connect_code TEXT"))
            users = conn.execute(text("SELECT id FROM users")).fetchall()
            for (uid,) in users:
                code = _generate_connect_code()
                conn.execute(text("UPDATE users SET connect_code=:c WHERE id=:id"),
                             {"c": code, "id": uid})
            conn.commit()
        # admin が誰もいなければ最初の登録者（id が最小）を admin に昇格させる
            first = conn.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1")).fetchone()
            if first:
                conn.execute(text("UPDATE users SET role='admin' WHERE id=:id"),
                             {"id": first[0]})
            conn.commit()
        # config テーブルに user_id がなければ追加
        if "config" in inspector.get_table_names():
            config_columns = [c["name"] for c in inspector.get_columns("config")]
            if "user_id" not in config_columns:
                conn.execute(text("ALTER TABLE config ADD COLUMN user_id INTEGER"))
                first_user = conn.execute(text("SELECT id FROM users LIMIT 1")).fetchone()
                if first_user:
                    conn.execute(text("UPDATE config SET user_id=:uid"), {"uid": first_user[0]})
                conn.commit()
        # config テーブルに emergency_unblock がなければ追加
        if "config" in inspector.get_table_names():
            config_columns = [c["name"] for c in inspector.get_columns("config")]
            if "emergency_unblock" not in config_columns:
                conn.execute(text("ALTER TABLE config ADD COLUMN emergency_unblock BOOLEAN DEFAULT FALSE"))
                conn.commit()
        # config テーブルに version がなければ追加
        if "config" in inspector.get_table_names():
            config_columns = [c["name"] for c in inspector.get_columns("config")]
            if "version" not in config_columns:
                conn.execute(text("ALTER TABLE config ADD COLUMN version INTEGER DEFAULT 0"))
                conn.commit()
        # 既存ユーザーの user_points エントリが未作成なら初期化する
        users = conn.execute(text("SELECT id FROM users")).fetchall()
        for (uid,) in users:
            exists = conn.execute(
                text("SELECT user_id FROM user_points WHERE user_id=:uid"), {"uid": uid}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO user_points (user_id, season_points, lifetime_points) VALUES (:uid, 0, 0)"),
                    {"uid": uid}
                )
        conn.commit()
        # sites テーブルに user_id がなければ追加
        if "sites" in inspector.get_table_names():
            site_columns = [c["name"] for c in inspector.get_columns("sites")]
            if "user_id" not in site_columns:
                conn.execute(text("ALTER TABLE sites ADD COLUMN user_id INTEGER"))
                first_user = conn.execute(text("SELECT id FROM users LIMIT 1")).fetchone()
                if first_user:
                    conn.execute(text("UPDATE sites SET user_id=:uid"), {"uid": first_user[0]})
                conn.commit()
        # ユーザー動向記録カラムを追加（PostgreSQLはDATETIME非対応のためTIMESTAMPを使う）
        user_columns_now = [c["name"] for c in inspector.get_columns("users")]
        for col in ("registered_at", "first_save_at", "first_sync_at", "last_active_at"):
            if col not in user_columns_now:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} TIMESTAMP"))
        conn.commit()
        # Stripe サブスクリプション管理カラムを追加
        user_columns_now = [c["name"] for c in inspector.get_columns("users")]
        for col in ("stripe_customer_id", "stripe_subscription_id"):
            if col not in user_columns_now:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} TEXT"))
        for col in ("trial_ends_at", "plan_expires_at"):
            if col not in user_columns_now:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} TIMESTAMP"))
        conn.commit()
        # ストリーク保護シールドカラムを追加
        user_columns_now = [c["name"] for c in inspector.get_columns("users")]
        if "streak_shield" not in user_columns_now:
            conn.execute(text("ALTER TABLE users ADD COLUMN streak_shield INTEGER DEFAULT 0"))
        if "shield_refreshed_at" not in user_columns_now:
            conn.execute(text("ALTER TABLE users ADD COLUMN shield_refreshed_at TIMESTAMP"))
        conn.commit()


def create_user(username, password, email=None):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now   = datetime.datetime.now(JST).replace(tzinfo=None)
    token = secrets.token_urlsafe(32)
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(username=username).first()
        if user:
            user.password = generate_password_hash(password)
        else:
            # 最初のユーザーを自動的に admin にする
            is_first = session.query(UserModel).count() == 0
            user = UserModel(
                username=username,
                password=generate_password_hash(password),
                api_token=token,
                plan="free",
                role="admin" if is_first else "user",
                connect_code=_generate_connect_code(),
                email=email.lower().strip() if email else None,
                registered_at=now,
            )
            session.add(user)
            session.flush()
            session.add(Config(user_id=user.id, block_start="08:00", block_end="21:00"))
            session.add(UserPoints(user_id=user.id, season_points=0, lifetime_points=0))
        session.commit()


def get_user_by_id(user_id):
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        return {"id": user.id, "username": user.username, "api_token": user.api_token,
                "plan": user.plan or "free", "role": user.role or "user",
                "connect_code": user.connect_code, "email": user.email,
                "last_active_at": user.last_active_at,
                "stripe_customer_id": user.stripe_customer_id,
                "plan_expires_at": user.plan_expires_at} if user else None


def get_user_by_token(token):
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(api_token=token).first()
        return {"id": user.id, "username": user.username} if user else None


def verify_password(username, password):
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            return {"id": user.id, "username": user.username}
    return None


def load_config(user_id):
    with Session(engine) as session:
        config = session.query(Config).filter_by(user_id=user_id).first()
        sites  = session.query(Site).filter_by(user_id=user_id).all()
        if not config:
            return {"block_start": "08:00", "block_end": "21:00", "sites": []}
        apps = session.query(App).filter_by(user_id=user_id).all()
        return {
            "version":          config.version or 0,
            "block_start":      config.block_start,
            "block_end":        config.block_end,
            "sites":            [s.domain for s in sites],
            "apps":             [a.path for a in apps],
            "emergency_unblock": config.emergency_unblock or False,
        }


def set_emergency_unblock(user_id, value: bool):
    with Session(engine) as session:
        config = session.query(Config).filter_by(user_id=user_id).first()
        if config:
            config.emergency_unblock = value
            config.version = (config.version or 0) + 1
            session.commit()


def add_event_log(user_id, event):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    with Session(engine) as session:
        session.add(EventLog(
            user_id    = user_id,
            event      = event,
            # SQLAlchemyのDateTimeはtzinfoなしを期待するため、JSTで取得後にtzinfoを除去する
            created_at = datetime.datetime.now(JST).replace(tzinfo=None),
        ))
        session.commit()


def get_streak(user_id):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        last_emergency = (
            session.query(EventLog)
            .filter_by(user_id=user_id, event="emergency_unblock")
            .order_by(EventLog.id.desc())
            .first()
        )
        # 緊急解除したことがある場合：最後の緊急解除から何日経つか
        if last_emergency:
            return max(0, (now - last_emergency.created_at).days)
        # 一度も緊急解除していない場合：最初のブロック開始から何日経つか
        first_block = (
            session.query(EventLog)
            .filter_by(user_id=user_id, event="block_start")
            .order_by(EventLog.id.asc())
            .first()
        )
        if first_block:
            return (now - first_block.created_at).days
        return 0


def get_event_logs(user_id, limit=30):
    with Session(engine) as session:
        logs = (
            session.query(EventLog)
            .filter_by(user_id=user_id)
            .order_by(EventLog.id.desc())
            .limit(limit)
            .all()
        )
        return [{"event": l.event, "created_at": l.created_at.strftime("%m/%d %H:%M:%S")} for l in logs]


def get_success_rate(user_id, days: int) -> dict:
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now   = datetime.datetime.now(JST).replace(tzinfo=None)
    since = now - datetime.timedelta(days=days)
    with Session(engine) as session:
        # 計測開始点: 初回block_startとsinceの遅い方（使い始めより前は数えない）
        first_block = (
            session.query(EventLog)
            .filter_by(user_id=user_id, event="block_start")
            .order_by(EventLog.id.asc())
            .first()
        )
        if not first_block:
            return {"rate": None, "success": 0, "total": 0}
        window_start = max(since, first_block.created_at)
        # 経過日数（今日は未完了のため含めない）
        total = (now.date() - window_start.date()).days
        if total <= 0:
            return {"rate": None, "success": 0, "total": 0}
        # 緊急解除があった日を失敗としてカウントする
        emergency_logs = (
            session.query(EventLog)
            .filter(
                EventLog.user_id == user_id,
                EventLog.event == "emergency_unblock",
                EventLog.created_at >= window_start,
            )
            .all()
        )
    failure = len({log.created_at.date() for log in emergency_logs})
    success = max(0, total - failure)
    rate    = round(success / total * 100) if total > 0 else None
    return {"rate": rate, "success": success, "total": total}


def has_emergency_history(user_id) -> bool:
    with Session(engine) as session:
        return session.query(EventLog).filter_by(
            user_id=user_id, event="emergency_unblock"
        ).first() is not None


def set_user_plan(user_id, plan: str):
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            user.plan = plan
            session.commit()


def _generate_connect_code() -> str:
    # 大文字英数字6文字。O・0・I・1 は視認性が悪いので除外する
    alphabet = [c for c in (string.ascii_uppercase + string.digits)
                if c not in ("O", "0", "I", "1")]
    return ''.join(secrets.choice(alphabet) for _ in range(6))


def get_user_by_connect_code(code: str):
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(connect_code=code.upper()).first()
        return {"id": user.id, "api_token": user.api_token} if user else None


def get_user_by_email(email: str):
    # メールアドレスからユーザーを検索する（パスワードリセット用）
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(email=email.lower().strip()).first()
        return {"id": user.id, "username": user.username, "email": user.email} if user else None


def set_user_email(user_id: int, email: str):
    # プロフィールページからメールアドレスを登録・更新する
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            user.email = email.lower().strip()
            session.commit()


def create_reset_token(user_id: int) -> str:
    JST = datetime.timezone(datetime.timedelta(hours=9))
    token = secrets.token_urlsafe(32)
    expires_at = datetime.datetime.now(JST).replace(tzinfo=None) + datetime.timedelta(hours=1)
    with Session(engine) as session:
        # 既存の未使用トークンをすべて無効化してから新規発行する（1ユーザー1トークン）
        session.query(PasswordResetToken).filter_by(user_id=user_id, used=False).update({"used": True})
        session.add(PasswordResetToken(
            user_id=user_id,
            token=token,
            expires_at=expires_at,
            used=False,
        ))
        session.commit()
    return token


def verify_reset_token(token: str):
    """トークンが有効かどうかを確認する。有効なら user_id を含む dict を返す。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        t = session.query(PasswordResetToken).filter_by(token=token, used=False).first()
        if not t or t.expires_at < now:
            return None
        return {"user_id": t.user_id}


def consume_reset_token(token: str):
    """トークンを使用済みにしてuser_idを返す。無効なら None を返す。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        t = session.query(PasswordResetToken).filter_by(token=token, used=False).first()
        if not t or t.expires_at < now:
            return None
        t.used = True
        user_id = t.user_id
        session.commit()
    return user_id


def update_password(user_id: int, new_password: str):
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            user.password = generate_password_hash(new_password)
            session.commit()


_RANKS = [
    (45, "Diamond"),
    (30, "Gold"),
    (15, "Iron"),
    (5,  "Flame"),
    (0,  "Seed"),
]


def get_rank(season_points: int) -> dict:
    for i, (threshold, name) in enumerate(_RANKS):
        if season_points >= threshold:
            if i == 0:
                return {"name": name, "next": None, "progress": 100, "pts_to_next": 0}
            next_threshold, next_name = _RANKS[i - 1]
            pts_in_tier = season_points - threshold
            tier_size   = next_threshold - threshold
            progress    = round(pts_in_tier / tier_size * 100)
            return {"name": name, "next": next_name,
                    "progress": min(progress, 99),
                    "pts_to_next": next_threshold - season_points}
    return {"name": "Seed", "next": "Flame", "progress": 0, "pts_to_next": 5}


def check_season_reset():
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST)
    with Session(engine) as session:
        latest = session.query(Season).order_by(Season.id.desc()).first()
        if latest and latest.year == now.year and latest.month == now.month:
            return  # 同じ月なのでリセット不要
        session.add(Season(year=now.year, month=now.month,
                           created_at=now.replace(tzinfo=None)))
        # 初回以外はシーズンポイントをリセットする
        if latest:
            session.query(UserPoints).update({"season_points": 0})
        session.commit()


def _compute_streak_in_session(session, user_id, now):
    last_emergency = (
        session.query(EventLog)
        .filter_by(user_id=user_id, event="emergency_unblock")
        .order_by(EventLog.id.desc())
        .first()
    )
    if last_emergency:
        return max(0, (now - last_emergency.created_at).days)
    first_block = (
        session.query(EventLog)
        .filter_by(user_id=user_id, event="block_start")
        .order_by(EventLog.id.asc())
        .first()
    )
    return (now - first_block.created_at).days if first_block else 0


def _record_point(session, pts, user_id, amount, reason, now, affect_lifetime):
    pts.season_points = max(0, pts.season_points + amount)
    if affect_lifetime and amount > 0:
        pts.lifetime_points += amount
    session.add(PointLog(user_id=user_id, amount=amount, reason=reason, created_at=now))


def apply_event_points(user_id: int, event: str):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        pts = session.get(UserPoints, user_id)
        if not pts:
            pts = UserPoints(user_id=user_id, season_points=0, lifetime_points=0)
            session.add(pts)

        if event == "block_end":
            # 直近のblock_start以降にemergency_unblockがあれば失敗セッション
            last_start = (
                session.query(EventLog)
                .filter_by(user_id=user_id, event="block_start")
                .order_by(EventLog.id.desc())
                .first()
            )
            had_emergency = last_start and session.query(EventLog).filter(
                EventLog.user_id == user_id,
                EventLog.event == "emergency_unblock",
                EventLog.created_at > last_start.created_at,
            ).first() is not None

            if not had_emergency:
                _record_point(session, pts, user_id, 1, "daily_completion", now, affect_lifetime=True)
                streak = _compute_streak_in_session(session, user_id, now)
                if streak > 0 and streak % 30 == 0:
                    _record_point(session, pts, user_id, 10, f"streak_{streak}", now, affect_lifetime=True)
                elif streak > 0 and streak % 7 == 0:
                    _record_point(session, pts, user_id, 3, f"streak_{streak}", now, affect_lifetime=True)

        elif event == "emergency_unblock":
            # シーズンポイントのみ -6pt（0未満にしない）
            deduct = min(6, pts.season_points)
            if deduct > 0:
                _record_point(session, pts, user_id, -deduct, "emergency_unblock", now, affect_lifetime=False)

        session.commit()


def get_user_points(user_id) -> dict:
    with Session(engine) as session:
        pts = session.get(UserPoints, user_id)
        if not pts:
            return {"season_points": 0, "lifetime_points": 0}
        return {"season_points": pts.season_points, "lifetime_points": pts.lifetime_points}


def get_season_ranking(limit=10) -> list:
    with Session(engine) as session:
        rows = (
            session.query(UserModel.username, UserPoints.season_points, UserPoints.lifetime_points)
            .join(UserPoints, UserModel.id == UserPoints.user_id)
            .order_by(UserPoints.season_points.desc())
            .limit(limit)
            .all()
        )
        return [{"username": r[0], "season_points": r[1], "lifetime_points": r[2]} for r in rows]


def record_first_save(user_id: int):
    """ブロック設定を初めて保存したタイミングを記録する。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            if user.first_save_at is None:
                user.first_save_at = now
            user.last_active_at = now
            session.commit()


def record_first_sync(user_id: int):
    """エージェントが初めてconfigを取得したタイミングを記録する。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            if user.first_sync_at is None:
                user.first_sync_at = now
            user.last_active_at = now
            session.commit()


def get_analytics() -> dict:
    """管理者用ダッシュボード向けにユーザー動向の集計値を返す。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        total  = session.query(UserModel).count()
        saved  = session.query(UserModel).filter(UserModel.first_save_at.isnot(None)).count()
        synced = session.query(UserModel).filter(UserModel.first_sync_at.isnot(None)).count()

        def active_within(days: int) -> int:
            since = now - datetime.timedelta(days=days)
            return session.query(UserModel).filter(UserModel.last_active_at >= since).count()

        return {
            "total":      total,
            "saved":      saved,
            "synced":     synced,
            "active_2d":  active_within(2),
            "active_7d":  active_within(7),
            "active_14d": active_within(14),
            "active_30d": active_within(30),
        }


def add_app_to_config(user_id: int, path: str) -> bool:
    """エージェントのトレイUIからアプリパスを追加する。重複は無視してFalseを返す。"""
    with Session(engine) as session:
        exists = session.query(App).filter_by(user_id=user_id, path=path).first()
        if exists:
            return False
        session.add(App(user_id=user_id, path=path))
        # バージョンをインクリメントして次のpollでエージェントに即時反映させる
        config = session.query(Config).filter_by(user_id=user_id).first()
        if config:
            config.version = (config.version or 0) + 1
        session.commit()
    return True


def set_stripe_subscription(user_id: int, customer_id, subscription_id, plan: str,
                             trial_ends_at=None, plan_expires_at=None):
    """Webhookイベントを受けてStripeサブスク情報とプランをまとめて更新する。"""
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if user:
            user.stripe_customer_id     = customer_id
            user.stripe_subscription_id = subscription_id
            user.plan                   = plan
            if trial_ends_at is not None:
                user.trial_ends_at = trial_ends_at
            if plan_expires_at is not None:
                user.plan_expires_at = plan_expires_at
            session.commit()


def get_user_by_stripe_customer_id(customer_id: str):
    """WebhookイベントのcustomerフィールドからユーザーIDを引く。"""
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(stripe_customer_id=customer_id).first()
        return {"id": user.id} if user else None


def get_streak_shield(user_id: int) -> int:
    """シールド枚数を返す。月が変わっていれば1にリセット（premiumのみ）。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if not user or user.plan != "premium":
            return 0
        refreshed_at = user.shield_refreshed_at
        if refreshed_at is None or (refreshed_at.year, refreshed_at.month) != (now.year, now.month):
            user.streak_shield = 1
            user.shield_refreshed_at = now
            session.commit()
            return 1
        return user.streak_shield or 0


def use_streak_shield(user_id: int) -> bool:
    """シールドを1枚消費する。成功したらTrueを返す。"""
    JST = datetime.timezone(datetime.timedelta(hours=9))
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        if not user or user.plan != "premium":
            return False
        refreshed_at = user.shield_refreshed_at
        if refreshed_at is None or (refreshed_at.year, refreshed_at.month) != (now.year, now.month):
            user.streak_shield = 1
            user.shield_refreshed_at = now
        current = user.streak_shield or 0
        if current <= 0:
            session.commit()
            return False
        user.streak_shield = current - 1
        session.commit()
        return True


def save_config(user_id, block_start, block_end, sites, apps=None):
    with Session(engine) as session:
        config = session.query(Config).filter_by(user_id=user_id).first()
        if not config:
            config = Config(user_id=user_id, block_start=block_start, block_end=block_end)
            session.add(config)
        else:
            config.block_start = block_start
            config.block_end   = block_end
        config.version = (config.version or 0) + 1
        session.query(Site).filter_by(user_id=user_id).delete()
        for domain in sites:
            session.add(Site(user_id=user_id, domain=domain))
        session.query(App).filter_by(user_id=user_id).delete()
        for path in (apps or []):
            session.add(App(user_id=user_id, path=path))
        session.commit()
