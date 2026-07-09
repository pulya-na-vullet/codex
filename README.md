# IT Workshop Order Manager

Web-based order management system for an IT workshop.

## Stack

- Python 3
- Flask (UI + routing)
- SQLite (`orders.db`)
- Jinja2 templates + Bootstrap 5

## Run

```bash
python3 -m pip install -r requirements.txt
python3 app.py
```

Then open:

`http://localhost:8000`

## Main sections

- Dashboard
- Orders / New Order / Order detail
- Clients (with RF phone validation and duplicate checks)
- Services (with category support)

## Notes

- Existing legacy database migrations and merge import are handled in `database.py`.
- The web interface is implemented in `webapp/`.
