import os
import json
import secrets
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
    plan      = Column(String, nullable=False, default="free")
    role      = Column(String, nullable=False, default="user")
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


def init_db():
    Base.metadata.create_all(engine)
    _migrate()


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
        # users テーブルに plan がなければ追加（デフォルト free）
        if "plan" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN plan TEXT DEFAULT 'free'"))
            conn.commit()
        # users テーブルに role がなければ追加（デフォルト user）
        if "role" not in user_columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'"))
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


def create_user(username, password):
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
                "plan": user.plan or "free", "role": user.role or "user"} if user else None


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
    now = datetime.datetime.now(JST).replace(tzinfo=None)
    since = now - datetime.timedelta(days=days)
    with Session(engine) as session:
        logs = (
            session.query(EventLog)
            .filter(EventLog.user_id == user_id, EventLog.created_at >= since)
            .all()
        )
    # 日ごとにブロックがあった日・緊急解除があった日を集計する
    days_with_block     = set()
    days_with_emergency = set()
    for log in logs:
        day = log.created_at.date()
        if log.event == "block_start":
            days_with_block.add(day)
        elif log.event == "emergency_unblock":
            days_with_emergency.add(day)
    total   = len(days_with_block)
    success = len(days_with_block - days_with_emergency)
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
