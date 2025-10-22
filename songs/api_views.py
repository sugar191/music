from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.utils.encoding import iri_to_uri
from rest_framework import status, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Artist, Song, Rating, ArtistSongView, Rating
from .api_serializers import (
    RatingExportSerializer,
    ArtistSongRowSerializer,
    RatingRowSerializer,
)
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
    # 1) まず song_id を見る（優先）
    sid = request.data.get("song_id")
    song = None
    if sid:
        try:
            song = (
                Song.objects.select_related("artist")
                .only("id", "title", "artist__name")
                .get(id=int(sid))
            )
        except Song.DoesNotExist:
            return Response({"detail": "song not found"}, status=404)

    # 2) 無ければ従来どおり artist/title で検索
    if song is None:
        artist = request.data.get("artist")
        title = request.data.get("title")
        score = request.data.get("score")
        if not artist or not title or score is None:
            return Response(
                {"detail": "song_id または artist/title/score が必要です"}, status=400
            )
        song = find_song_loose_readonly(artist, title)
        if not song:
            return Response({"detail": "song not found"}, status=404)
    else:
        score = request.data.get("score")
        if score is None:
            return Response({"detail": "score が必要です"}, status=400)

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


class ArtistSongExport(APIView):
    """
    GET /api/artist_song_view
      オプション:
        ?q=...          （artist/title の部分一致）
        ?artist=...     （artist_name の部分一致）
        ?title=...      （song_title の部分一致）
        ?limit=1000     （安全のための上限。未指定=全件）
    """

    permission_classes = [permissions.AllowAny]  # 公開でOKならこのまま

    def get(self, request):
        qs = ArtistSongView.objects.all().order_by("song_id")

        q = request.query_params.get("q")
        if q:
            qs = qs.filter(artist_name__icontains=q) | qs.filter(
                song_title__icontains=q
            )

        artist = request.query_params.get("artist")
        if artist:
            qs = qs.filter(artist_name__icontains=artist)

        title = request.query_params.get("title")
        if title:
            qs = qs.filter(song_title__icontains=title)

        # 取り過ぎ防止の軽い上限
        limit = request.query_params.get("limit")
        if limit:
            try:
                limit = max(1, min(100000, int(limit)))
                qs = qs[:limit]
            except ValueError:
                raise ValidationError({"limit": "must be integer"})

        data = ArtistSongRowSerializer(qs, many=True).data
        return Response(data)


class SongsRatingExport(APIView):
    """
    GET /api/songs_rating
      必須:
        ?user_id=1
      オプション:
        ?updated_after=ISO8601（差分抽出）
        ?limit=100000         （安全のための上限）
    """

    permission_classes = [permissions.IsAuthenticated]  # 個人データなので基本は要認証

    def get(self, request):
        user_id = request.query_params.get("user_id")
        if not user_id:
            raise ValidationError({"user_id": "this query param is required"})
        try:
            uid = int(user_id)
        except ValueError:
            raise ValidationError({"user_id": "must be integer"})

        qs = (
            Rating.objects.select_related("song")
            .filter(user_id=uid)
            .order_by("song_id")
        )

        ua = request.query_params.get("updated_after")
        if ua:
            dt = parse_datetime(ua)
            if not dt:
                raise ValidationError({"updated_after": "invalid datetime"})
            qs = qs.filter(updated_at__gte=dt)

        limit = request.query_params.get("limit")
        if limit:
            try:
                limit = max(1, min(100000, int(limit)))
                qs = qs[:limit]
            except ValueError:
                raise ValidationError({"limit": "must be integer"})

        data = RatingRowSerializer(qs, many=True).data
        return Response(data)
