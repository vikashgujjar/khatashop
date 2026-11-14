"""WSGI entry point for other servers, e.g. on Linux:

    gunicorn --workers 3 --bind 0.0.0.0:5000 --reload wsgi:app
"""
from app import create_app

app = create_app()
