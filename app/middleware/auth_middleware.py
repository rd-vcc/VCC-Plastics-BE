"""Authentication middleware placeholder.

There is no real auth enforcement today: the frontend does not send an
``Authorization`` header on any request, and no endpoint currently checks a
token or session. This module intentionally contains no logic and is NOT
registered on the app in ``main.py``. Do not add enforcement here (or wire it
into ``main.py``) without a corresponding frontend change - doing so would
reject every request from the live frontend.
"""
