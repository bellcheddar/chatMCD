"""Gunicorn entry point.

    gunicorn --config deploy/gunicorn.conf.py wsgi:app
"""

from chatmcd.app import create_app

app = create_app()
