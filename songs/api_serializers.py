from rest_framework import serializers
from .models import Artist, Song, Rating
from .utils import normalize


class ArtistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Artist
        fields = ["id", "name", "format_name", "region"]

    def validate(self, attrs):
        name = attrs.get("name") or (self.instance and self.instance.name)
        if name:
            attrs["format_name"] = normalize(name)
        return attrs


class SongSerializer(serializers.ModelSerializer):
    artist_name = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = Song
        fields = ["id", "title", "format_title", "artist", "artist_name", "is_cover"]

    def validate(self, attrs):
        title = attrs.get("title") or (self.instance and self.instance.title)
        if title:
            attrs["format_title"] = normalize(title)
        return attrs


class RatingSerializer(serializers.ModelSerializer):
    artist = serializers.CharField(write_only=True)
    title = serializers.CharField(write_only=True)
    score = serializers.IntegerField(min_value=0, max_value=100)

    class Meta:
        model = Rating
        fields = ["song", "score", "artist", "title"]  # song は応答で埋まることあり
        extra_kwargs = {"song": {"read_only": True}}
