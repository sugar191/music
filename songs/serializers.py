from rest_framework import serializers
from .models import MusicRegion, Artist, Song, Rating


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
        artist_name = validated["artist"].strip()
        title = validated["title"].strip()

        # ★ 地域を完全に無視し、artist.name と song.title の組み合わせだけを大小無視で検索
        qs = (
            Song.objects.select_related("artist")
            .filter(artist__name__iexact=artist_name, title__iexact=title)
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
        elif cnt == 0:
            raise serializers.ValidationError({"detail": "not_found"})
        else:
            # 同名アーティスト（地域違い等）で複数候補があるケース
            raise serializers.ValidationError(
                {
                    "detail": "multiple_matches",
                    "candidates": [
                        {
                            "song_id": s.id,
                            "artist_id": s.artist_id,
                            "artist": s.artist.name,
                            "title": s.title,
                        }
                        for s in qs[:20]
                    ],
                }
            )

    def create(self, validated):
        """
        ?create=true のときだけ呼ばれる想定。
        作る前に lookup_only を必ず試し、既存があればそれを返す。
        地域は使わず region=None 固定で作成。
        """
        try:
            return self.lookup_only(validated)
        except serializers.ValidationError as e:
            if not (
                isinstance(e.detail, dict) and e.detail.get("detail") == "not_found"
            ):
                raise  # multiple_matches 等はそのまま上げる

        from .models import Artist

        artist_name = validated["artist"].strip()
        title = validated["title"].strip()
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
