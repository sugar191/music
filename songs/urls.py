from django.urls import path
from django.contrib.auth import views as auth_views
from .views import (
    artist_ranking_view,
    song_list_view,
    update_rating_view,
    bulk_add_view,
    signup_view,
)

urlpatterns = [
    path("ranking/", artist_ranking_view, name="artist_ranking"),
    path("songs/", song_list_view, name="song_list"),
    path("update-rating/", update_rating_view, name="update_rating"),
    path("songs/bulk_add/", bulk_add_view, name="bulk_add_songs"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("signup/", signup_view, name="signup"),
]
