from flask import Flask

from webapp.auth import configure_auth
from webapp.routes import register_routes


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "itm-workshop-secret-change-in-production"
    configure_auth(app)
    register_routes(app)
    return app
