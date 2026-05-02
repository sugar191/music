from django.db import migrations


# 旧 rank_view の DDL（reverse 用に保持）
# 元の sql/create_view.sql の rank_view 部分と同等。
RANK_VIEW_CREATE_SQL = """
CREATE OR REPLACE VIEW rank_view AS
SELECT
  r.id                                   AS rank_id,
  a.id                                   AS artist_id,
  a.name                                 AS artist_name,
  a.region_id                            AS region_id,
  s.id                                   AS song_id,
  s.title                                AS song_title,
  r.user_id                              AS user_id,
  r.score                                AS score,

  RANK()       OVER (PARTITION BY r.user_id, a.id
                     ORDER BY r.score DESC)                  AS rank_artist,
  ROW_NUMBER() OVER (PARTITION BY r.user_id, a.id
                     ORDER BY r.score DESC, UPPER(s.title))  AS order_artist,

  RANK()       OVER (PARTITION BY r.user_id
                     ORDER BY r.score DESC)                  AS rank_all,
  ROW_NUMBER() OVER (PARTITION BY r.user_id
                     ORDER BY r.score DESC, UPPER(a.name), UPPER(s.title)) AS order_all,

  RANK()       OVER (PARTITION BY r.user_id, a.region_id
                     ORDER BY r.score DESC)                  AS rank_region,
  ROW_NUMBER() OVER (PARTITION BY r.user_id, a.region_id
                     ORDER BY r.score DESC, UPPER(a.name), UPPER(s.title)) AS order_region

FROM songs_artist a
JOIN songs_song   s ON a.id = s.artist_id
JOIN songs_rating r ON s.id = r.song_id
WHERE s.is_cover = 0
"""


class Migration(migrations.Migration):

    dependencies = [
        ('songs', '0017_alter_rating_score'),
    ]

    operations = [
        # 1) DB 上のビューを破棄。RankView は managed = False なので
        #    DeleteModel ではテーブル/ビューに対する SQL は出ない。
        migrations.RunSQL(
            sql="DROP VIEW IF EXISTS rank_view",
            reverse_sql=RANK_VIEW_CREATE_SQL,
        ),
        # 2) Django 側のモデル状態からも RankView を削除。
        migrations.DeleteModel(
            name='RankView',
        ),
    ]
