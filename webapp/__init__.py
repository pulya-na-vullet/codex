from flask import Flask

from database import Database
from webapp.routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev-change-me"
    app.db = Database()
    register_routes(app)
    return app
