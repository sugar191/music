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
)

urlpatterns = [
    path("ranking/", ranking_view, name="ranking"),
    path("artists/", artist_list_view, name="artist_list"),
    path("artists/<int:artist_id>/songs/", artist_song_list_view, name="artist_songs"),
    path("songs/", song_list_view, name="song_list"),
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
]
