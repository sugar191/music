from django.urls import path
from . import api_views

urlpatterns = [
    path("artists/", api_views.artist_list),
    # 名義（ArtistCredit）の一覧。Song.credit がこの id を参照しているので、
    # 同期スクリプトは artists/ → artist_credits/ → songs/ の順で流し込む。
    # 末尾の songs/update_credits は作詞・作曲のことで、名義とは別物。
    path("artist_credits/", api_views.artist_credit_list),
    # 別表記（ArtistAlias）の一覧。名義とは別テーブル。
    path("artist_aliases/", api_views.artist_alias_list),
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
    path(
        "songs/update_credits",
        api_views.update_song_credits,
        name="api_update_song_credits",
    ),
]
