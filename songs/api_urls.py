from django.urls import path
from . import api_views

urlpatterns = [
    path("ratings/score", api_views.get_score, name="get_score"),  # GET
    path("ratings/score/update", api_views.update_score, name="update"),  # POST
    path(
        "ratings/export", api_views.export_ratings, name="export_ratings"
    ),  # ★ 追加: GET
    path(
        "artists/regions", api_views.export_artist_regions, name="export_artist_regions"
    ),
    path(
        "artist_song_view",
        api_views.ArtistSongExport.as_view(),
        name="artist-song-export",
    ),
    path(
        "songs_rating",
        api_views.SongsRatingExport.as_view(),
        name="songs-rating-export",
    ),
]
