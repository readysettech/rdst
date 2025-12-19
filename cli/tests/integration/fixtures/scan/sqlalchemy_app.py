"""
SQLAlchemy service for IMDB title queries.
Used as a fixture for rdst scan integration tests.
"""
from sqlalchemy import create_engine, func, Column, Integer, String, Boolean, Numeric, Text
from sqlalchemy.orm import Session, declarative_base

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


class TitleRatings(Base):
    __tablename__ = "title_ratings"
    tconst = Column(Text, primary_key=True)
    averagerating = Column(Numeric)
    numvotes = Column(Integer)


def get_recent_movies(session: Session):
    """Get recent movies ordered by year."""
    return (
        session.query(TitleBasics)
        .filter(TitleBasics.titletype == "movie")
        .filter(TitleBasics.startyear > 2000)
        .order_by(TitleBasics.startyear.desc())
        .limit(10)
        .all()
    )


def get_top_rated_titles(session: Session):
    """Get top rated titles with high vote counts."""
    return (
        session.query(TitleBasics.primarytitle, TitleRatings.averagerating, TitleRatings.numvotes)
        .join(TitleRatings, TitleBasics.tconst == TitleRatings.tconst)
        .filter(TitleRatings.numvotes > 1000)
        .order_by(TitleRatings.averagerating.desc())
        .limit(25)
        .all()
    )


def get_type_counts(session: Session):
    """Count titles by type - no LIMIT (anti-pattern)."""
    return (
        session.query(TitleBasics.titletype, func.count(TitleBasics.tconst))
        .group_by(TitleBasics.titletype)
        .all()
    )
