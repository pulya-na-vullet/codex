from __future__ import annotations

import os

from webapp.network import print_access_urls


def main():
    from webapp import create_app

    host = os.getenv("IT_MASTER_HOST", "0.0.0.0")
    port = int(os.getenv("IT_MASTER_PORT", "8000"))
    print_access_urls(host, port)

    app = create_app()
    # threaded=True: несколько менеджеров могут работать одновременно
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
