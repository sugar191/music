from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from .models import Artist, Song, Rating
from .api_serializers import RatingSerializer
from .utils import normalize


def find_song_loose_readonly(artist_name: str, title: str):
    fn = normalize(artist_name)
    ft = normalize(title)
    qs = Song.objects.select_related("artist")
    # 1) 厳密一致（format_*）
    song = qs.filter(artist__format_name=fn, format_title=ft).first()
    if song:
        return song
    # 2) フォールバック（DBは書かない）
    return qs.filter(
        artist__name__iexact=artist_name.strip(), title__iexact=title.strip()
    ).first()


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def get_score(request):
    artist = request.query_params.get("artist")
    title = request.query_params.get("title")
    if not artist or not title:
        return Response({"detail": "artist と title が必要です"}, status=400)

    song = find_song_loose_readonly(artist, title)
    if not song:
        return Response({"detail": "song not found"}, status=404)

    rating = Rating.objects.filter(user=request.user, song=song).first()
    if not rating:
        return Response({"detail": "rating not found"}, status=404)

    return Response(
        {
            "artist": song.artist.name,
            "title": song.title,
            "score": rating.score,
            "song_id": song.id,
        }
    )


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated])
def update_score(request):
    artist = request.data.get("artist")
    title = request.data.get("title")
    score = request.data.get("score")
    if not artist or not title or score is None:
        return Response({"detail": "artist/title/score が必要です"}, status=400)

    song = find_song_loose_readonly(artist, title)
    if not song:
        return Response({"detail": "song not found"}, status=404)

    rating, created = Rating.objects.update_or_create(
        user=request.user, song=song, defaults={"score": int(score)}
    )
    return Response(
        {
            "artist": song.artist.name,
            "title": song.title,
            "score": rating.score,
            "song_id": song.id,
            "created": created,
        }
    )
