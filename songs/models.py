from django.contrib.auth.models import User
from django.db import models


class MusicRegion(models.Model):
    code = models.CharField(max_length=10, unique=True)  # 例: JP, EN, KR
    name = models.CharField(max_length=100)  # 例: 邦楽, 洋楽, K-POP

    def __str__(self):
        return self.name


class Artist(models.Model):
    name = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化した名前（songs.utils.normalize）
    format_name = models.CharField(max_length=100, null=True, blank=True)
    region = models.ForeignKey(
        MusicRegion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="artists",
    )

    class Meta:
        # 既定の並び順は 地域 → 名前 の昇順
        ordering = ["region", "name"]
        # 同じ地域内で歌手名は重複させない（地域が違えば同名を許容）
        unique_together = (
            "name",
            "region",
        )

    def __str__(self):
        return self.name


class Song(models.Model):
    title = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化したタイトル（songs.utils.normalize）
    format_title = models.CharField(max_length=100, null=True, blank=True)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="songs")
    # カバー曲フラグ。NULL は「未判定」を意味する。
    # 注意: ランキングのSQLは is_cover = 0 で絞るため、NULL の曲は集計対象外になる。
    is_cover = models.BooleanField(
        null=True,
        blank=True,
    )
    lyricist = models.CharField(max_length=200, null=True, blank=True)
    composer = models.CharField(max_length=200, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)

    class Meta:
        # 同じ歌手の中で曲名は重複させない
        unique_together = (
            "title",
            "artist",
        )
        # 作詞/作曲/年ランキングの絞り込み用インデックス
        indexes = [
            models.Index(fields=["lyricist"]),
            models.Index(fields=["composer"]),
            models.Index(fields=["year"]),
        ]

    def __str__(self):
        return self.title


class Rating(models.Model):
    """
    ユーザー×曲の採点。score（好み度）と karaoke_score（カラオケ採点機の点数）は
    片方だけ入っていることがあるため、どちらも NULL 可。
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="ratings")
    score = models.IntegerField(null=True, blank=True)  # 好み度（0〜100）
    karaoke_score = models.DecimalField(
        max_digits=6, decimal_places=3, null=True, blank=True
    )  # カラオケ採点機能の点数（0.000〜100.000）
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "song")  # 同じユーザーは1曲に1回だけ評価可能

        # ランキング集計（user_id で絞って score 降順）用
        indexes = [
            models.Index(fields=["user", "score"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.song.title} : {self.score}"


class ArtistYearPreference(models.Model):
    """
    アーティスト × 年 の好き度 (0〜4)。

    score=0 は「好きではない」ではなく「未設定」を意味し、
    画面側の保存処理では 0 になった行は残さず削除する。
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
    """
    ユーザーの補足情報（年表ヒートマップの年齢行に使う生年）。
    1ユーザー1件なので OneToOneField（DB側の UNIQUE 制約で重複を防ぐ）。
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    birth_year = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} profile"
