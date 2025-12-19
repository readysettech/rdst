"""
Skippable and edge case query patterns for rdst scan integration tests.
Contains queries that should be extracted but skipped during ORM-to-SQL conversion,
plus one valid query for contrast.
"""
from sqlalchemy.orm import Session
from sqlalchemy import Column, Integer, String, Text, Boolean, Numeric
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class TitleBasics(Base):
    __tablename__ = "title_basics"
    tconst = Column(Text, primary_key=True)
    titletype = Column(Text)
    primarytitle = Column(Text)
    originaltitle = Column(Text)
    isadult = Column(Boolean)
    startyear = Column(Integer)
    runtimeminutes = Column(Integer)
    genres = Column(Text)


def fetch_without_execute(session: Session):
    """Result fetch only - cursor.fetchall() without preceding execute()."""
    cursor = session.connection().cursor()
    return cursor.fetchall()


def dynamic_filter(session: Session, **kwargs):
    """Dynamic arguments - kwargs expanded at runtime."""
    return session.query(TitleBasics).filter_by(**kwargs).all()


def get_valid_movie(session: Session):
    """Valid query - should extract and convert to SQL successfully."""
    return session.query(TitleBasics).filter(
        TitleBasics.titletype == "movie",
    ).first()
