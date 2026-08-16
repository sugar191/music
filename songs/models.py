from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .utils import normalize


class MusicRegion(models.Model):
    code = models.CharField(max_length=10, unique=True)  # 例: JP, EN, KR
    name = models.CharField(max_length=100)  # 例: 邦楽, 洋楽, K-POP

    def __str__(self):
        return self.name


class Artist(models.Model):
    """
    ランキングの集計単位となる「アーティスト実体」。

    実体と名義（クレジット）は別レイヤーなので混同しないこと。
    サザンオールスターズ名義の曲と桑田佳祐名義の曲を1つのランキングに
    まとめたい場合、Artist は「サザンオールスターズ」1件だけを作り、
    名義の違いは ArtistCredit 側で表現する。
    """

    name = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化した名前（songs.utils.normalize）。
    # ここは「正規化名」専用。別名義を入れる場所ではない（ArtistCredit を使うこと）。
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

    def primary_credit(self):
        """主名義の ArtistCredit を返す。移行後は必ず1件存在する前提。"""
        return self.credits.filter(is_primary=True).first()

    def ensure_primary_credit(self):
        """
        主名義の ArtistCredit を返す。無ければ作る。

        移行(0022)で全 Artist に1件作られるが、admin で消された場合や
        移行後に別経路で作られた Artist もありうるので、取得側で
        作り直せるようにしておく。Song.credit は NOT NULL なので、
        ここが None を返すと曲を登録できなくなってしまう。
        """
        credit = self.credits.filter(is_primary=True).first()
        if credit:
            return credit

        credit, _created = ArtistCredit.objects.get_or_create(
            artist=self,
            name=self.name,
            defaults={"format_name": normalize(self.name), "is_primary": True},
        )
        if not credit.is_primary:
            credit.is_primary = True
            credit.save(update_fields=["is_primary"])
        return credit

    def resolve_credit(self, credit_name=None):
        """
        名義名から ArtistCredit を取得する（無ければ作る）。

        credit_name が空、または Artist.name と同じなら主名義を返す。
        「桑田佳祐」のような別名義が来たら、その名義行を作って返す。
        """
        name = (credit_name or "").strip()
        if not name or name == self.name:
            return self.ensure_primary_credit()

        fmt = normalize(name)
        credit = self.credits.filter(
            models.Q(name=name) | models.Q(format_name=fmt)
        ).first()
        if credit:
            return credit

        credit, _created = ArtistCredit.objects.get_or_create(
            artist=self,
            name=name,
            defaults={"format_name": fmt, "is_primary": False},
        )
        return credit

    def add_alias(self, name, kind=None):
        """
        別表記を1件登録する（既にあれば既存を返す）。

        名義（ArtistCredit）ではなく別表記（ArtistAlias）を足したいときはこちら。
        「その名前で出た曲があるか」で使い分ける。
        """
        name = (name or "").strip()
        if not name or name == self.name:
            return None

        alias, _created = ArtistAlias.objects.get_or_create(
            artist=self,
            name=name,
            defaults={
                "format_name": normalize(name),
                "kind": kind or ArtistAlias.KIND_OTHER,
            },
        )
        return alias

    def search_names(self):
        """
        この歌手を指す名前を全部返す（本名・名義・別表記）。重複は除く。

        外部API検索の代替形を組み立てるときなど、「この歌手を指しうる文字列」が
        まとめて欲しい場面で使う。
        """
        names = [self.name]
        names += [c.name for c in self.credits.all()]
        names += [a.name for a in self.aliases.all()]
        seen, out = set(), []
        for n in names:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out


class ArtistCredit(models.Model):
    """
    曲がリリースされたときの「名義」。

    Artist が実体（ランキングの単位）で、ArtistCredit はその実体が使う名前。
    例: Artist「サザンオールスターズ」に対して
        - ArtistCredit「サザンオールスターズ」(is_primary=True)
        - ArtistCredit「桑田佳祐」
        - ArtistCredit「KUWATA BAND」
    のように複数ぶら下がり、Song.credit がそのどれかを必ず指す。

    is_primary が1 Artist につき1件であることは DB 制約では守れない。
    MySQL は部分ユニークインデックス（Django の UniqueConstraint(condition=...)）を
    サポートしないため、アプリ側とマイグレーションで担保する。
    """

    artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, related_name="credits"
    )
    name = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化した名義（songs.utils.normalize）
    format_name = models.CharField(max_length=100, blank=True, default="")
    # 主名義かどうか。通常は Artist.name と同じ表記の行が True になる。
    is_primary = models.BooleanField(default=False)

    class Meta:
        # 主名義を先頭に、あとは名前順
        ordering = ["artist", "-is_primary", "name"]
        # 同じ Artist の中で名義表記は重複させない。
        # format_name 側にユニークを張らないのは、「Queen」と「QUEEN」のように
        # 正規化すると同じになる別名義を登録したくなる余地を残すため。
        unique_together = (
            "artist",
            "name",
        )
        indexes = [
            models.Index(fields=["format_name"]),
        ]

    def __str__(self):
        return self.name


class ArtistAlias(models.Model):
    """
    同じ歌手を指す「別表記」。ArtistCredit（名義）とは別物なので混同しないこと。

    両者の判別基準は「Song が指す必要があるか」。
      - 名義   … その名前で出た曲が実在し、曲ごとに使い分ける（安全地帯 と 玉置浩二）。
                  Song.credit の参照先になる。
      - 別表記 … 同じ相手を指す書き方違いで、曲を紐付ける必要はない
                  （髙橋真梨子 と 高橋真梨子、ビートルズ と The Beatles）。
                  検索で当てるためだけに持つ。

    同じテーブルにまとめない理由は、Song.credit が別表記行を指せてしまうのを
    DB で防げないため（MySQL では部分ユニークインデックスが使えず、アプリ側の
    不変条件が増えるだけになる）。

    1歌手に複数の別表記を持てる。format_name を Artist から追い出した結果、
    外部API検索用の代替表記の置き場が無くなったが、その受け皿でもある。
    """

    KIND_SYMBOL = "symbol"
    KIND_SCRIPT = "script"
    KIND_KANA = "kana"
    KIND_ORDER = "order"
    KIND_VARIANT = "variant"
    KIND_OTHER = "other"
    KIND_CHOICES = [
        (KIND_SYMBOL, "記号・空白の違い"),  # C-C-B / C‐C‐B
        (KIND_SCRIPT, "英字・原語表記"),  # ビートルズ / The Beatles
        (KIND_KANA, "読み・カナ表記"),  # ↑THE HIGH-LOWS↓ / ザ・ハイロウズ
        (KIND_ORDER, "語順・区切りの違い"),  # 山内賢 & 和泉雅子 / 和泉雅子 & 山内賢
        (KIND_VARIANT, "異体字"),  # 髙橋真梨子 / 高橋真梨子
        (KIND_OTHER, "その他の表記ゆれ"),
    ]

    artist = models.ForeignKey(
        Artist, on_delete=models.CASCADE, related_name="aliases"
    )
    name = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化した別表記（songs.utils.normalize）
    format_name = models.CharField(max_length=100, blank=True, default="")
    # 分類。ロジックには効かないが、仕分けの結果を残しておくために持つ。
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default=KIND_OTHER)

    class Meta:
        ordering = ["artist", "name"]
        # 同じ Artist の中で別表記は重複させない
        unique_together = (
            "artist",
            "name",
        )
        indexes = [
            models.Index(fields=["format_name"]),
        ]

    def __str__(self):
        return self.name


def find_artist_by_any_name(name, region=None):
    """
    表記ゆれ・別名義を含めて Artist を1件探す（見つからなければ None）。

    「桑田佳祐」（名義）でも「ザ・ハイロウズ」（別表記）でも、Artist 本体に
    辿り着けるように、Artist.name / Artist.format_name に加えて
    ArtistCredit と ArtistAlias の両方を見る。
    別名義や別表記を Artist.format_name に入れていた頃はそちらで引っかかって
    いたが、format_name が正規化名専用に戻ったので、その役目はここが引き継ぐ。
    """
    name = (name or "").strip()
    if not name:
        return None

    fmt = normalize(name)
    qs = Artist.objects.all()
    if region is not None:
        qs = qs.filter(region=region)

    return (
        qs.filter(
            models.Q(name=name)
            | models.Q(format_name=fmt)
            | models.Q(credits__name=name)
            | models.Q(credits__format_name=fmt)
            | models.Q(aliases__name=name)
            | models.Q(aliases__format_name=fmt)
        )
        .distinct()
        .first()
    )


class Song(models.Model):
    title = models.CharField(max_length=100)
    # 検索・突き合わせ用に NFKC 正規化 + 小文字化したタイトル（songs.utils.normalize）
    format_title = models.CharField(max_length=100, null=True, blank=True)
    artist = models.ForeignKey(Artist, on_delete=models.CASCADE, related_name="songs")
    # この曲がどの名義で出たか。主名義の曲も必ず行を指す（NULL 不可）。
    #
    # NULL を「主名義」の意味に使わないのは、MySQL の UNIQUE インデックスが
    # NULL 同士を別の値として扱うため。将来セルフカバーを別曲として持ちたく
    # なったとき unique_together を ("title", "artist", "credit") に広げるが、
    # credit が NULL 可だと主名義の曲で重複防止が効かなくなってしまう。
    #
    # 削除は RESTRICT。名義行をうっかり消して曲まで道連れになるのを防ぐ
    # （名義を消す前に、その名義の曲を別の名義へ付け替える必要がある）。
    # PROTECT ではなく RESTRICT なのは、Artist を削除したときに
    # 「曲も名義も CASCADE で一緒に消える」ケースを通すため。PROTECT だと
    # この連鎖削除まで止めてしまい、歌手を削除できなくなる。
    credit = models.ForeignKey(
        ArtistCredit, on_delete=models.RESTRICT, related_name="songs"
    )
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
        # 同じ歌手の中で曲名は重複させない。
        # セルフカバー（同じ曲名がサザン名義と桑田佳祐名義の両方にある）を
        # 別の曲として持ちたくなったら、ここに "credit" を足す。あわせて
        # views._upsert_song と api_views.add_song の既存曲探索にも
        # credit を条件として加えること（DB制約とは別に効いているため）。
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

    def clean(self):
        """
        credit が別の Artist のものになっていないか検査する。

        Song.artist と Song.credit.artist は同じ実体を指すはずだが、
        FK は他テーブルをまたいだ整合性まではチェックしてくれない
        （CheckConstraint も他テーブルを参照できない）。
        admin や ModelForm 経由の保存はここで弾ける。
        """
        super().clean()
        if (
            self.credit_id
            and self.artist_id
            and self.credit.artist_id != self.artist_id
        ):
            raise ValidationError(
                {"credit": "名義が別の歌手のものです。"},
            )

    @property
    def credit_name(self):
        """表示用の名義。N+1 を避けるため select_related('credit') 推奨。"""
        return self.credit.name

    @property
    def is_alias_credit(self):
        """主名義以外（例: 桑田佳祐名義）なら True。表示の出し分けに使う。"""
        return not self.credit.is_primary


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
