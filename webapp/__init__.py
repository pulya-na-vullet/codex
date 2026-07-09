from flask import Flask

from webapp.routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "dev-change-me"
    register_routes(app)
    return app
