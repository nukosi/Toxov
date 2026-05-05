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
    config    = relationship("Config", back_populates="user", uselist=False, cascade="all, delete-orphan")
    sites     = relationship("Site", back_populates="user", cascade="all, delete-orphan")


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


class EventLog(Base):
    __tablename__ = "event_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    event      = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False)


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
            user = UserModel(
                username=username,
                password=generate_password_hash(password),
                api_token=token,
            )
            session.add(user)
            session.flush()
            session.add(Config(user_id=user.id, block_start="08:00", block_end="21:00"))
        session.commit()


def get_user_by_id(user_id):
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        return {"id": user.id, "username": user.username, "api_token": user.api_token} if user else None


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
        return {
            "version":          config.version or 0,
            "block_start":      config.block_start,
            "block_end":        config.block_end,
            "sites":            [s.domain for s in sites],
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


def save_config(user_id, block_start, block_end, sites):
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
        session.commit()
