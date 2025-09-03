from rest_framework import serializers
from .models import MusicRegion, Artist, Song, Rating
import unicodedata


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "").strip()


class MusicRegionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MusicRegion
        fields = ["id", "code", "name"]


class ArtistSerializer(serializers.ModelSerializer):
    region = MusicRegionSerializer(read_only=True)

    class Meta:
        model = Artist
        fields = ["id", "name", "format_name", "region"]


class SongSerializer(serializers.ModelSerializer):
    artist = ArtistSerializer(read_only=True)

    class Meta:
        model = Song
        fields = ["id", "title", "format_title", "is_cover", "artist"]


class RatingSerializer(serializers.ModelSerializer):
    """
    自分の採点をCRUDするためのSerializer。
    POST/PATCHでは song はIDで渡します。
    """

    song = serializers.PrimaryKeyRelatedField(queryset=Song.objects.all())
    score = serializers.IntegerField(min_value=0, max_value=100)

    class Meta:
        model = Rating
        fields = ["id", "song", "score", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SongLookupCreateSerializer(serializers.Serializer):
    artist = serializers.CharField(max_length=100)
    title = serializers.CharField(max_length=100)
    region_code = serializers.CharField(
        max_length=10, required=False, allow_null=True, allow_blank=True
    )
    is_cover = serializers.BooleanField(required=False, allow_null=True)

    def lookup_only(self, validated):
        in_artist = _norm(validated["artist"])
        in_title = _norm(validated["title"])

        # ★ 地域は完全に無視。artist.name と song.title の組み合わせだけで大小無視で一致
        qs = (
            Song.objects.select_related("artist")
            .filter(artist__name__iexact=in_artist, title__iexact=in_title)
            .order_by("id")
        )

        cnt = qs.count()
        if cnt == 1:
            s = qs.first()
            return {
                "song_id": s.id,
                "artist_id": s.artist_id,
                "created": {"region": False, "artist": False, "song": False},
            }
        if cnt > 1:
            # 候補を返して 409 にする（作成はしない）
            raise serializers.ValidationError(
                {
                    "detail": "multiple_matches",
                    "candidates": [
                        {
                            "song_id": x.id,
                            "artist_id": x.artist_id,
                            "artist": x.artist.name,
                            "title": x.title,
                        }
                        for x in qs[:50]
                    ],
                }
            )

        # ゆるめ検索の候補（見つからない時のヒント用・作成はしない）
        qs2 = (
            Song.objects.select_related("artist")
            .filter(artist__name__icontains=in_artist, title__icontains=in_title)
            .order_by("artist__name", "title")[:50]
        )
        if qs2.exists():
            raise serializers.ValidationError(
                {
                    "detail": "multiple_matches",
                    "hint": "exact not found; showing icontains candidates.",
                    "candidates": [
                        {
                            "song_id": x.id,
                            "artist_id": x.artist_id,
                            "artist": x.artist.name,
                            "title": x.title,
                        }
                        for x in qs2
                    ],
                    "echo": {"artist_in": in_artist, "title_in": in_title},
                }
            )

        raise serializers.ValidationError(
            {
                "detail": "not_found",
                "echo": {"artist_in": in_artist, "title_in": in_title},
            }
        )

    def create(self, validated):
        """?create=true の時だけ作成。まず lookup を試す。地域は使わず region=None 固定。"""
        try:
            return self.lookup_only(validated)
        except serializers.ValidationError as e:
            if not (
                isinstance(e.detail, dict) and e.detail.get("detail") == "not_found"
            ):
                raise

        artist_name = _norm(validated["artist"])
        title = _norm(validated["title"])
        is_cover = validated.get("is_cover", None)

        artist, c_artist = Artist.objects.get_or_create(
            name=artist_name, region=None, defaults={"format_name": artist_name}
        )
        song, c_song = Song.objects.get_or_create(
            title=title,
            artist=artist,
            defaults={"format_title": title, "is_cover": is_cover},
        )
        if is_cover is not None and song.is_cover != is_cover:
            song.is_cover = is_cover
            song.save(update_fields=["is_cover"])

        return {
            "song_id": song.id,
            "artist_id": artist.id,
            "created": {"region": False, "artist": c_artist, "song": c_song},
        }
