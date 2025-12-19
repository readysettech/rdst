"""
Django service for IMDB title queries.
Used as a fixture for rdst scan integration tests.
"""
from django.db import models
from django.db.models import Count, Avg, Q


class TitleBasics(models.Model):
    tconst = models.CharField(max_length=255, primary_key=True)
    titletype = models.CharField(max_length=255)
    primarytitle = models.CharField(max_length=255)
    originaltitle = models.CharField(max_length=255)
    isadult = models.BooleanField()
    startyear = models.IntegerField()
    runtimeminutes = models.IntegerField()
    genres = models.CharField(max_length=255)

    class Meta:
        db_table = "title_basics"


class TitleRatings(models.Model):
    tconst = models.CharField(max_length=255, primary_key=True)
    averagerating = models.DecimalField(max_digits=10, decimal_places=1)
    numvotes = models.IntegerField()

    class Meta:
        db_table = "title_ratings"


def get_movie_by_id(tconst: str):
    """Get a specific movie by its ID."""
    return TitleBasics.objects.filter(
        tconst=tconst,
    ).first()


def get_genre_stats():
    """Aggregate title counts and avg runtime by type."""
    return TitleBasics.objects.values("titletype").annotate(
        count=Count("tconst"),
        avg_runtime=Avg("runtimeminutes"),
    ).aggregate(total=Count("tconst"))


def get_latest_title():
    """Get the most recently released title - uses latest() terminal."""
    return TitleBasics.objects.filter(
        titletype="movie",
    ).latest("startyear")
