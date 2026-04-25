import os
import json
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import declarative_base, Session
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

_url = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'nukosisnsblocker.db')}")
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url)
Base = declarative_base()


class Config(Base):
    __tablename__ = "config"
    id          = Column(Integer, primary_key=True)
    block_start = Column(String, nullable=False, default="08:00")
    block_end   = Column(String, nullable=False, default="21:00")


class Site(Base):
    __tablename__ = "sites"
    id     = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String, nullable=False, unique=True)


class UserModel(Base):
    __tablename__ = "users"
    id       = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)


def init_db():
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.get(Config, 1):
            _migrate_from_json(session)
            session.commit()


def _migrate_from_json(session):
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        session.add(Config(id=1,
                           block_start=data.get("block_start", "08:00"),
                           block_end=data.get("block_end", "21:00")))
        for domain in data.get("sites", []):
            session.add(Site(domain=domain))
        print("config.json からデータを移行しました")
    else:
        session.add(Config(id=1, block_start="08:00", block_end="21:00"))


def load_config():
    with Session(engine) as session:
        config = session.get(Config, 1)
        sites  = session.query(Site).order_by(Site.id).all()
        return {
            "block_start": config.block_start,
            "block_end":   config.block_end,
            "sites":       [s.domain for s in sites],
        }


def save_config(block_start, block_end, sites):
    with Session(engine) as session:
        config = session.get(Config, 1)
        config.block_start = block_start
        config.block_end   = block_end
        session.query(Site).delete()
        for domain in sites:
            session.add(Site(domain=domain))
        session.commit()


def create_user(username, password):
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(username=username).first()
        if user:
            user.password = generate_password_hash(password)
        else:
            session.add(UserModel(username=username,
                                  password=generate_password_hash(password)))
        session.commit()


def get_user_by_id(user_id):
    with Session(engine) as session:
        user = session.get(UserModel, user_id)
        return {"id": user.id, "username": user.username} if user else None


def verify_password(username, password):
    with Session(engine) as session:
        user = session.query(UserModel).filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            return {"id": user.id, "username": user.username}
    return None
