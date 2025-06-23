from django.urls import path
from .views import artist_ranking_view, song_detail, rate_song, song_list_view

urlpatterns = [
    path("ranking/", artist_ranking_view, name="artist_ranking"),
    path("songs/<int:song_id>/", song_detail, name="song_detail"),
    path("songs/<int:song_id>/rate/", rate_song, name="rate_song"),
    path("songs/", song_list_view, name="song_list"),
]
