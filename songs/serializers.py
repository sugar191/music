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
