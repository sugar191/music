"""
仕分け結果にしたがって、ArtistCredit から「別表記」89 件を ArtistAlias へ移す。

0022 は Artist.format_name に入っていた値をすべて ArtistCredit（別名義）に
してしまったが、実際に仕分けたところ名義は 32 件だけで、残りは同じ相手を指す
書き方違いだった。ここでその 89 件を ArtistAlias へ移し、ArtistCredit からは
消す。

移す対象は (credit_id, name) のペアで指定する。id だけで指定しないのは、
別環境や別時点で id がずれていた場合に、無関係な行を消してしまわないため。
名前が食い違う行・既に存在しない行は、警告を出してスキップする。

曲がぶら下がっている名義行は移さない（Song.credit は RESTRICT なので削除に
失敗するし、曲が紐付いている時点でそれは「名義」として使われている）。
仕分け時点では 124 件すべて song_count = 0 だったので、通常は 0 件のはず。

kind は仕分けシートの「種別」列から機械的に振ったもの。
  symbol=記号・空白 / script=英字・原語 / kana=読み・カナ
  order=語順・区切り / variant=異体字 / other=その他
"""

from django.db import migrations


# (ArtistCredit.id, ArtistCredit.name, ArtistAlias.kind)
ALIAS_ROWS = [
    (2, '¿?shimon', 'other'),
    (4, 'Alexandros', 'symbol'),
    (8, 'ザ・ハイロウズ', 'kana'),
    (15, '19(ジューク)', 'kana'),
    (17, "19's Sound Factory", 'other'),
    (25, '40mP', 'symbol'),
    (57, 'アリヨウ', 'kana'),
    (68, 'B’z', 'symbol'),
    (74, 'バービーボーイズ', 'kana'),
    (93, 'C‐C‐B', 'symbol'),
    (98, 'CHAGE&ASKA', 'symbol'),
    (111, 'CircusP', 'symbol'),
    (121, 'cosMo(暴走P)', 'symbol'),
    (123, 'クレイジーケンバンド', 'kana'),
    (125, 'Creepy Nuts(R-指定&DJ松永)', 'other'),
    (128, 'D51', 'symbol'),
    (151, 'EasyPop', 'symbol'),
    (173, 'Folder 5', 'symbol'),
    (177, "FUNKY MONKEY BΛBY'S", 'symbol'),
    (189, 'GO‐BANG’S', 'symbol'),
    (205, 'Hi‐STANDARD', 'symbol'),
    (208, 'ヒルクライム', 'kana'),
    (254, 'KOH⁺', 'symbol'),
    (260, 'L’Arc〜en〜Ciel', 'symbol'),
    (262, 'L-R', 'symbol'),
    (276, 'm‐flo', 'symbol'),
    (286, 'May’n', 'symbol'),
    (293, 'MIMI & WHITEBOX', 'symbol'),
    (314, 'NSP', 'symbol'),
    (342, 'PEOPLE1', 'symbol'),
    (379, 'SHOGUN', 'symbol'),
    (381, 'SHOW‐YA', 'symbol'),
    (397, 'SOUL’d OUT', 'symbol'),
    (404, 'シュガー', 'kana'),
    (422, 'Team.ねこかん[猫]', 'symbol'),
    (436, 'THE 虎舞竜', 'symbol'),
    (442, 'Tommy february⁶', 'symbol'),
    (1751, 'Earth Wind & Fire', 'script'),
    (498, '陳美齡', 'script'),
    (1753, 'Eagles', 'script'),
    (1755, 'Van Halen', 'script'),
    (1760, 'The Carpenters', 'script'),
    (1762, 'Queen', 'script'),
    (590, 'キングトーンズ', 'symbol'),
    (598, 'Circus', 'script'),
    (1764, 'Simon & Garfunkel', 'script'),
    (619, 'SHEENA & THE ROKKETS', 'script'),
    (1766, 'The Jackson 5', 'script'),
    (636, '翁倩玉', 'script'),
    (638, 'JIRO’s', 'script'),
    (640, 'じん', 'other'),
    (645, 'Zoo Nee Voo', 'script'),
    (651, 'すし', 'other'),
    (653, 'Stardust Revue', 'script'),
    (1771, 'The Stylistics', 'script'),
    (658, 'スペシャルウィーク (CV:  和氣あず未 ),  サイレンススズカ (CV:  高野麻里佳 ),  トウカイテイオー (CV:  Machico )', 'symbol'),
    (671, 'チーム・ハナヤマタ', 'symbol'),
    (686, '鄧麗君', 'script'),
    (1779, 'The Beatles', 'script'),
    (750, 'ひとしずく × やま△', 'symbol'),
    (1783, 'Billy Joel', 'script'),
    (755, 'Billy Banban', 'script'),
    (757, 'ヒロシ＆キーボー', 'symbol'),
    (762, 'Four Clovers', 'script'),
    (764, 'Four Saints', 'script'),
    (776, 'PRINCESS PRINCESS', 'script'),
    (781, 'バーチャル・シンガー ,  Leo/need ,  MORE MORE JUMP! ,  Vivid BAD SQUAD ,  ワンダーランズ×ショウタイム  &  25時、ナイトコードで。', 'other'),
    (1787, 'Whitney Houston', 'script'),
    (1791, 'Bon Jovi', 'script'),
    (797, 'My Pace', 'script'),
    (1793, 'Michael Jackson', 'script'),
    (800, 'MICHAELS', 'script'),
    (1795, 'Madonna', 'script'),
    (1800, 'Lady Gaga', 'script'),
    (855, 'REBECCA', 'script'),
    (1802, 'Wham!', 'symbol'),
    (1042, '三浦弘とハニー・シックス', 'symbol'),
    (1056, '和泉雅子 & 山内賢', 'order'),
    (1059, '山本コウタローとウィークエンド', 'symbol'),
    (1099, '小林 旭', 'symbol'),
    (1160, '初音ミク ,  星乃一歌 ,  花里みのり ,  小豆沢こはね ,  天馬司  &  宵崎奏', 'order'),
    (1206, '泉こなた （ 平野綾 ）、 柊かがみ （ 加藤英美里 ）、 柊つかさ （ 福原香織 ）、 高良みゆき （ 遠藤綾 ）', 'symbol'),
    (1234, '津山洋子 ・ 大木英夫', 'order'),
    (1357, '敏いとう と ハッピー&ブルー', 'symbol'),
    (1434, '涼宮ハルヒ  ( 平野綾 )', 'symbol'),
    (1436, '涼宮ハルヒ (CV: 平野綾 )、 長門有希 (CV: 茅原実里 )、 朝比奈みくる (CV: 後藤邑子 )', 'symbol'),
    (1446, '和田たけあき', 'other'),
    (1455, '徳永英明', 'variant'),
    (1457, '高橋真梨子', 'variant'),
]


def to_alias(apps, schema_editor):
    ArtistCredit = apps.get_model("songs", "ArtistCredit")
    ArtistAlias = apps.get_model("songs", "ArtistAlias")
    Song = apps.get_model("songs", "Song")

    moved = skipped_missing = skipped_name = skipped_songs = 0

    for credit_id, name, kind in ALIAS_ROWS:
        credit = ArtistCredit.objects.filter(id=credit_id).first()
        if credit is None:
            skipped_missing += 1
            continue
        if credit.name != name:
            print(
                f"[warn] credit id={credit_id} の名前が想定と違うためスキップ: "
                f"想定={name!r} 実際={credit.name!r}"
            )
            skipped_name += 1
            continue
        if Song.objects.filter(credit_id=credit.id).exists():
            print(
                f"[warn] credit id={credit_id} ({credit.name!r}) には曲が"
                "紐付いているためスキップ（名義として使われている）"
            )
            skipped_songs += 1
            continue

        ArtistAlias.objects.get_or_create(
            artist_id=credit.artist_id,
            name=credit.name,
            defaults={"format_name": credit.format_name or "", "kind": kind},
        )
        credit.delete()
        moved += 1

    print(
        f"[0025] 別表記へ移動 {moved} 件 / "
        f"スキップ: 行なし={skipped_missing}, 名前不一致={skipped_name}, "
        f"曲あり={skipped_songs}"
    )


def back_to_credit(apps, schema_editor):
    """逆方向。別表記を is_primary=False の名義行として戻す。"""
    ArtistCredit = apps.get_model("songs", "ArtistCredit")
    ArtistAlias = apps.get_model("songs", "ArtistAlias")

    for alias in ArtistAlias.objects.all().iterator():
        ArtistCredit.objects.get_or_create(
            artist_id=alias.artist_id,
            name=alias.name,
            defaults={"format_name": alias.format_name or "", "is_primary": False},
        )
    ArtistAlias.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("songs", "0024_artistalias"),
    ]

    operations = [
        migrations.RunPython(to_alias, back_to_credit),
    ]
