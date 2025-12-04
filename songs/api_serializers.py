from rest_framework import serializers
from .models import Artist, Song, Rating, ArtistSongView
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
    artist = serializers.CharField(write_only=True, required=False)
    title = serializers.CharField(write_only=True, required=False)
    song_id = serializers.IntegerField(write_only=True, required=False)
    score = serializers.IntegerField(min_value=0, max_value=100)

    class Meta:
        model = Rating
        fields = ["song", "score", "artist", "title", "song_id"]  # song は read_only
        extra_kwargs = {"song": {"read_only": True}}

    def validate(self, attrs):
        # song_id があれば artist/title は不要（後方互換のため両方許容）
        sid = attrs.get("song_id")
        artist = attrs.get("artist")
        title = attrs.get("title")
        if not sid and not (artist and title):
            raise serializers.ValidationError("song_id または artist/title が必要です")
        return attrs


class RatingExportSerializer(serializers.Serializer):
    artist = serializers.CharField()
    title = serializers.CharField()
    score = serializers.IntegerField(min_value=0, max_value=100)


class ArtistRegionSerializer(serializers.ModelSerializer):
    region = serializers.SerializerMethodField()

    class Meta:
        model = Artist
        fields = ["name", "format_name", "region"]

    def get_region(self, obj):
        return obj.region.code if obj.region else None


class ArtistSongRowSerializer(serializers.ModelSerializer):
    # SQLite 側に合わせたキー名へ変換（artist_name -> artist, song_title -> title）
    artist = serializers.CharField(source="artist_name")
    title = serializers.CharField(source="song_title")

    class Meta:
        model = ArtistSongView
        fields = ["song_id", "artist", "title"]  # 必要十分。増やすならここに追加


class RatingRowSerializer(serializers.ModelSerializer):
    # song_id, user_id を数値で返す
    song_id = serializers.IntegerField(source="song.id", read_only=True)
    user_id = serializers.IntegerField(source="user.id", read_only=True)

    class Meta:
        model = Rating
        fields = [
            "user_id",
            "song_id",
            "score",
            "created_at",
            "updated_at",
        ]
