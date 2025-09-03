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

    def create(self, validated):
        artist_name = validated["artist"].strip()
        title = validated["title"].strip()
        region_code = (validated.get("region_code") or "").strip().upper() or None
        is_cover = validated.get("is_cover", None)

        created = {"region": False, "artist": False, "song": False}

        region = None
        if region_code:
            region, created_region = MusicRegion.objects.get_or_create(
                code=region_code,
                defaults={"name": region_code},
            )
            created["region"] = created_region

        artist, created_artist = Artist.objects.get_or_create(
            name=artist_name,
            region=region,
            defaults={"format_name": artist_name},
        )
        created["artist"] = created_artist

        song, created_song = Song.objects.get_or_create(
            title=title,
            artist=artist,
            defaults={"format_title": title, "is_cover": is_cover},
        )
        # is_cover を後から渡された場合は更新（任意）
        if is_cover is not None and song.is_cover != is_cover:
            song.is_cover = is_cover
            song.save(update_fields=["is_cover"])

        created["song"] = created_song

        return {
            "song_id": song.id,
            "artist_id": artist.id,
            "created": created,
        }
