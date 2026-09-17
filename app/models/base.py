"""SQLAlchemy declarative base for FUTURE models.

Existing tables continue to be accessed via the raw-SQL repository/service
functions ported from the original flat-layout routers - nothing here models
every existing table. This is scaffolding for incremental adoption only.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
