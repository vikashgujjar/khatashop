"""WSGI entry point, for running the app under another WSGI server if one is available
(the app does not need one: `python3 run.py` uses its own standard-library server), e.g.:

    gunicorn --workers 3 --bind 0.0.0.0:5000 wsgi:app
"""
from app import create_app

app = create_app()
