"""
rate_limiter.py — Singleton SlowAPI Limiter instance.

Extracted into its own module to break the circular import between:
  app.main (imports intake router)  ↔  app.routers.intake (needs limiter)

Both main.py and intake.py import the limiter from here.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Global limiter instance — key by remote IP address
limiter = Limiter(key_func=get_remote_address)
