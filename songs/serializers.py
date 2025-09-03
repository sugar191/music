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
    # 使わないが互換のため残す
    region_code = serializers.CharField(
        max_length=10, required=False, allow_null=True, allow_blank=True
    )
    is_cover = serializers.BooleanField(required=False, allow_null=True)

    def lookup_only(self, validated):
        """地域を無視して、artist.name と song.title の組み合わせだけで厳密に探す。大小無視。"""
        artist_name = validated["artist"].strip()
        title = validated["title"].strip()

        # name__iexact / title__iexact で大小無視（地域は完全に無視）
        from .models import Artist, Song

        artists = Artist.objects.filter(name__iexact=artist_name)
        songs = Song.objects.filter(
            title__iexact=title, artist__in=artists
        ).select_related("artist")

        count = songs.count()
        if count == 1:
            s = songs.first()
            return {
                "song_id": s.id,
                "artist_id": s.artist_id,
                "created": {"region": False, "artist": False, "song": False},
            }
        elif count == 0:
            # 見つからない
            raise serializers.ValidationError({"detail": "not_found"})
        else:
            # 複数一致（地域違い・重複など）
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
                        for s in songs[:20]  # 多すぎると重いので上限
                    ],
                }
            )

    def create(self, validated):
        """互換のため残す: create=true の時だけ呼ぶ。地域は使わず、artist(region=None) に紐づけて作成。"""
        from .models import MusicRegion, Artist, Song

        artist_name = validated["artist"].strip()
        title = validated["title"].strip()
        is_cover = validated.get("is_cover", None)

        # まず lookup を試す（既存があればそれを返す）
        try:
            return self.lookup_only(validated)
        except serializers.ValidationError as e:
            # not_found のときだけ作成、それ以外（multiple）はそのまま投げる
            if not isinstance(e.detail, dict) or e.detail.get("detail") != "not_found":
                raise

        # 地域は使わない: region=None 固定
        artist, created_artist = Artist.objects.get_or_create(
            name=artist_name, region=None, defaults={"format_name": artist_name}
        )
        song, created_song = Song.objects.get_or_create(
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
            "created": {
                "region": False,
                "artist": created_artist,
                "song": created_song,
            },
        }
