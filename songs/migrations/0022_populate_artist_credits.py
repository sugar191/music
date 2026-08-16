"""
既存データを名義（ArtistCredit）モデルへ移行する。

やること:
1. 全 Artist に「主名義」の ArtistCredit を1件ずつ作る（name は Artist.name と同じ）。
2. Artist.format_name に別名義が入っている行を救出して ArtistCredit にする。
   例: name="サザンオールスターズ" / format_name="桑田佳祐" のケース。
   本来 format_name は正規化名を入れる列だが、別名義の置き場として
   使われていたため、正規化名と一致しない値を「別名義」とみなす。
3. Artist.format_name を本来の正規化名に戻す。
4. 既存の曲は全ていったん主名義に紐付ける。
   どの曲が桑田佳祐名義なのかはデータからは判定できないため、
   付け替えは移行後に admin から手作業で行う。

normalize() を songs.utils から import せず写しているのは、
将来 utils 側の正規化ルールを変えたときに、過去のマイグレーションの
挙動まで変わってしまうのを防ぐため（マイグレーションは書いた時点の
挙動で再現できる必要がある）。
"""

import unicodedata

from django.db import migrations


def _normalize(s):
    """0022 時点の songs.utils.normalize と同じ処理。"""
    if s is None:
        return ""
    return unicodedata.normalize("NFKC", s).lower().strip()


def populate_credits(apps, schema_editor):
    Artist = apps.get_model("songs", "Artist")
    ArtistCredit = apps.get_model("songs", "ArtistCredit")
    Song = apps.get_model("songs", "Song")

    for artist in Artist.objects.all().iterator():
        fmt = _normalize(artist.name)

        # 1) 主名義
        primary, _created = ArtistCredit.objects.get_or_create(
            artist_id=artist.id,
            name=artist.name,
            defaults={"format_name": fmt, "is_primary": True},
        )
        if not primary.is_primary:
            primary.is_primary = True
            primary.save(update_fields=["is_primary"])

        # 2) format_name に入っていた別名義を救出する。
        #    正規化名と一致する（＝本来の使い方をしている）場合は何もしない。
        old = (artist.format_name or "").strip()
        if old and old != fmt and old != artist.name:
            ArtistCredit.objects.get_or_create(
                artist_id=artist.id,
                name=old,
                defaults={"format_name": _normalize(old), "is_primary": False},
            )

        # 3) format_name を正規化名へ戻す
        if artist.format_name != fmt:
            artist.format_name = fmt
            artist.save(update_fields=["format_name"])

        # 4) この歌手の曲をいったん全て主名義へ
        Song.objects.filter(artist_id=artist.id, credit__isnull=True).update(
            credit=primary
        )


def unpopulate_credits(apps, schema_editor):
    """
    逆方向。曲の名義参照を外して名義行を消す。

    Artist.format_name に入っていた別名義は元に戻せない（正規化名で
    上書き済みのため）。巻き戻す場合は 0022 適用前のバックアップから
    format_name を復元すること。
    """
    ArtistCredit = apps.get_model("songs", "ArtistCredit")
    Song = apps.get_model("songs", "Song")

    Song.objects.update(credit=None)
    ArtistCredit.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("songs", "0021_artistcredit_song_credit"),
    ]

    operations = [
        migrations.RunPython(populate_credits, unpopulate_credits),
    ]
