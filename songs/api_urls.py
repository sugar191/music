from django.urls import path
from . import api_views

urlpatterns = [
    path("artists/", api_views.artist_list),
    path("songs/", api_views.song_list),
    path(
        "songs_rating",
        api_views.SongsRatingExport.as_view(),
        name="songs-rating-export",
    ),
    path("ratings/score/update", api_views.update_score, name="update"),  # POST
    path(
        "songs/create_with_artist",
        api_views.create_song_with_artist,
        name="create_song_with_artist",
    ),
]
