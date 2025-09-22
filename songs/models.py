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

    class Meta:
        unique_together = (
            "title",
            "artist",
        )  # ✅ アーティストと曲名の組み合わせを一意に

    def __str__(self):
        return self.title


class Rating(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="ratings")
    score = models.IntegerField()  # 点数（例: 0〜100）
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "song")  # 同じユーザーは1曲に1回だけ評価可能

        indexes = [
            models.Index(fields=["user", "score"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.song.title} : {self.score}"


class RankView(models.Model):
    rank_id = models.IntegerField(primary_key=True)
    artist_id = models.IntegerField()
    artist_name = models.CharField(max_length=100)
    region_id = models.IntegerField()
    song_id = models.IntegerField()
    song_title = models.CharField(max_length=100)
    user_id = models.IntegerField()
    score = models.IntegerField()
    rank_artist = models.IntegerField()
    order_artist = models.IntegerField()
    rank_all = models.IntegerField()
    order_all = models.IntegerField()
    rank_region = models.IntegerField()
    order_region = models.IntegerField()

    class Meta:
        managed = False  # DjangoがCREATE TABLEしないように
        db_table = "rank_view"

    def __str__(self):
        return f"{self.user_id} - {self.song_title} : {self.score}"
