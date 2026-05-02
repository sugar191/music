from django.contrib.auth.models import User
from django.db import models


class MusicRegion(models.Model):
    code = models.CharField(max_length=10, unique=True)  # 例: JP, EN, KR
    name = models.CharField(max_length=100)  # 例: 邦楽, 洋楽, K-POP

    def __str__(self):
        return self.name


class Artist(models.Model):
    name = models.CharField(max_length=100)
    format_name = models.CharField(max_length=100, null=True, blank=True)
    region = models.ForeignKey(
        MusicRegion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="artists",
    )

    class Meta:
        ordering = ["region", "name"]  # ← デフォルトで name 昇順
        unique_together = (
            "name",
            "region",
        )

    def __str__(self):
        return self.name


class Song(models.Model):
    title = models.CharField(max_length=100)
    format_title = models.CharField(max_length=100, null=True, blank=True)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="songs")
    is_cover = models.BooleanField(
        null=True,
        blank=True,
    )
    lyricist = models.CharField(max_length=200, null=True, blank=True)
    composer = models.CharField(max_length=200, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)

    class Meta:
        unique_together = (
            "title",
            "artist",
        )  # ✅ アーティストと曲名の組み合わせを一意に
        indexes = [
            models.Index(fields=["lyricist"]),
            models.Index(fields=["composer"]),
            models.Index(fields=["year"]),
        ]

    def __str__(self):
        return self.title


class Rating(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="ratings")
    score = models.IntegerField(null=True, blank=True)  # 好み度（例: 0〜100）
    karaoke_score = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )  # カラオケ採点機能の点数（例: 0.000〜100.000）
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "song")  # 同じユーザーは1曲に1回だけ評価可能

        indexes = [
            models.Index(fields=["user", "score"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.song.title} : {self.score}"


class ArtistYearPreference(models.Model):
    """
    アーティスト × 年 の好き度 (0〜4)
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, related_name="year_prefs"
    )
    year = models.IntegerField()
    score = models.IntegerField(default=0)  # 0〜4

    class Meta:
        unique_together = ("user", "artist", "year")
        indexes = [
            models.Index(fields=["user", "year"]),
            models.Index(fields=["user", "artist", "year"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.artist_id} {self.year}={self.score}"


class UserProfile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    birth_year = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} profile"
