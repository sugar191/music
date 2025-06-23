from django.contrib.auth.models import User
from django.db import models


class Artist(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Song(models.Model):
    title = models.CharField(max_length=100)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="songs")

    def __str__(self):
        return self.title


class Rating(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="ratings")
    score = models.IntegerField()  # 点数（例: 0〜100）
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "song")  # 同じユーザーは1曲に1回だけ評価可能

    def __str__(self):
        return f"{self.user.username} - {self.song.title} : {self.score}"
