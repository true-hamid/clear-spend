import os

from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "auth.login"


def create_app(config_overrides: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key-change-in-production"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "DATABASE_URL", f"sqlite:///{os.path.join(app.instance_path, 'clearspend.db')}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=32 * 1024 * 1024,  # 32MB total upload cap
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "1") == "1",
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if config_overrides:
        app.config.update(config_overrides)

    db.init_app(app)
    login_manager.init_app(app)

    from app.models import User

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    from app.auth import auth_bp
    from app.main import main_bp
    from app.admin import admin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    return app
