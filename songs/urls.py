from django.urls import path
from django.contrib.auth import views as auth_views
from .views import (
    ranking_view,
    artist_list_view,
    song_list_view,
    update_rating_view,
    update_cover_view,
    bulk_add_view,
    signup_view,
    missing_audio_files_view,
    artist_song_list_view,
    song_ranking_view,
    artist_search_view,
)
from . import views_dump

urlpatterns = [
    path("ranking/", ranking_view, name="ranking"),
    path("artists/", artist_list_view, name="artist_list"),
    path("song_ranking/", song_ranking_view, name="song_ranking"),
    path("artists/<int:artist_id>/songs/", artist_song_list_view, name="artist_songs"),
    path("songs/", song_list_view, name="song_list"),
    path("artist_search/", artist_search_view, name="artist_search"),
    path("update-rating/", update_rating_view, name="update_rating"),
    path("update-cover/", update_cover_view, name="update_cover"),
    path("songs/bulk_add/", bulk_add_view, name="bulk_add_songs"),
    path("missing-files/", missing_audio_files_view, name="missing_audio_files"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("signup/", signup_view, name="signup"),
    path("api/dump/run", views_dump.dump_tables, name="dump_tables"),
    path("api/dump/list", views_dump.list_dumps, name="list_dumps"),
    path("api/dump/download", views_dump.download_dump, name="download_dump"),
]
