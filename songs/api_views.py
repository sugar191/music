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


def export_ratings(request):
    """
    ログインユーザーの Rating を (artist, title, score) のJSON配列で返す。
    オプション:
      ?artist=...     → そのアーティストだけ（iexact）
      ?download=1     → Content-Disposition を付けてダウンロードさせやすく
    """
    qs = (
        Rating.objects.filter(user=request.user)
        .select_related("song__artist")
        .only("score", "song__title", "song__artist__name")
        .order_by("song__artist__name", "song__title")
    )

    artist = request.query_params.get("artist")
    if artist:
        qs = qs.filter(song__artist__name__iexact=artist.strip())

    data = [
        {
            "artist": r.song.artist.name,
            "title": r.song.title,
            "score": int(r.score),
        }
        for r in qs
    ]

    # （任意）バリデーションしてから返す
    ser = RatingExportSerializer(data=data, many=True)
    ser.is_valid(raise_exception=True)

    resp = Response(ser.data, status=200)

    # ダウンロードさせたい場合のヘッダ（?download=1）
    if request.query_params.get("download") in ("1", "true", "yes"):
        filename = f"ratings_{request.user.username}.json"
        resp["Content-Disposition"] = f'attachment; filename="{iri_to_uri(filename)}"'
    return resp
