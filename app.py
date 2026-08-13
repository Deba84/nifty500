"""Legacy web entry point — intentionally retired in scanner v6.2.

The old Flask app imported ``scan_stock()`` and performed 500 individual
Yahoo requests.  That function was removed in v6 because it caused severe
timeouts.  The supported production entry point is now ``daily_scan.py``,
which guarantees chunked batch downloads.

Keeping this small file gives old Render links a clear explanation without
silently reintroducing the slow architecture.
"""

MESSAGE = """
Nifty 500 Scanner v6.2 uses the GitHub Actions batch workflow.
The legacy per-stock Flask scanner is retired.
Run: python daily_scan.py
""".strip()


def app(environ, start_response):
    """Tiny dependency-free WSGI response for any old ``gunicorn app:app`` link."""
    body = (MESSAGE + "\n").encode("utf-8")
    start_response(
        "200 OK",
        [("Content-Type", "text/plain; charset=utf-8"), ("Content-Length", str(len(body)))],
    )
    return [body]


if __name__ == "__main__":
    print(MESSAGE)
