from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.encoding import iri_to_uri
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import Artist, Song, Rating
from .api_serializers import RatingExportSerializer
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


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def export_ratings(request):
    # ★ ここを request.GET に（DRFでもDjangoでもOK）
    params = request.GET
    artist = params.get("artist")
    want_download = params.get("download") in ("1", "true", "yes")

    qs = (
        Rating.objects.filter(user=request.user)
        .select_related("song__artist")
        .only("score", "song__title", "song__artist__name")
        .order_by("song__artist__name", "song__title")
    )
    if artist:
        qs = qs.filter(song__artist__name__iexact=artist.strip())

    data = [
        {"artist": r.song.artist.name, "title": r.song.title, "score": int(r.score)}
        for r in qs
    ]

    resp = Response(data, status=200)
    if want_download:
        filename = f"ratings_{request.user.username}.json"
        resp["Content-Disposition"] = f'attachment; filename="{iri_to_uri(filename)}"'
    return resp


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def export_artist_regions(request):
    """
    全アーティストの地域コードをエクスポート。
    ?download=1 で attachment
    """
    qs = Artist.objects.select_related("region").all().order_by("name")
    data = []
    for a in qs:
        fmt = a.format_name or normalize(a.name)
        data.append(
            {
                "name": a.name,
                "format_name": fmt,
                "region": (
                    a.region.code if a.region else None
                ),  # 例: "JP","EN","KR", None
            }
        )

    resp = Response(data)
    if request.query_params.get("download") in ("1", "true", "yes"):
        resp["Content-Disposition"] = 'attachment; filename="artist_regions.json"'
    return resp
