"""
Vercel entry point.

Vercel's Python runtime looks for a WSGI-callable named `app` in the file a
route is pointed at (see vercel.json). The real application lives in
app.py at the repo root; this file just re-exports it.
"""
from app import app  # noqa: F401
