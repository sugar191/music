from django.db import connection


# 作詞/作曲/年 ランキング用の対象カラム（SQLインジェクション対策のためホワイトリスト化）
# value: (DBカラム名, 数値カラムか)
_CREATOR_COLUMNS = {
    "lyricist": ("lyricist", False),
    "composer": ("composer", False),
    "year": ("year", True),
}


def _creator_filtered_cte(col, region_filter, is_numeric=False):
    """作詞/作曲/年ランキングの共通CTE: 評価済・非カバー・対象カラム入力済の曲を抽出"""
    # 数値カラム（year）は <> '' のチェックを外す（型エラー回避）
    empty_check = "" if is_numeric else f"AND s.{col} <> ''"
    return f"""
        WITH filtered AS (
            SELECT
                r.user_id,
                s.{col} AS creator,
                s.id AS song_id,
                s.title AS song_title,
                s.artist_id,
                a.name AS artist_name,
                r.score,
                a.region_id
            FROM songs_rating r
            JOIN songs_song s ON r.song_id = s.id
            JOIN songs_artist a ON s.artist_id = a.id
            WHERE r.user_id = %s
              AND s.is_cover = 0
              AND s.{col} IS NOT NULL
              {empty_check}
              {region_filter}
        ),
        counts AS (
            SELECT creator, COUNT(*) AS song_count FROM filtered GROUP BY creator
        )
    """


def call_creator_song_top_n(user_id, top_n, region_id, creator_type):
    """
    作詞者・作曲者・年ごとに、上位N曲の詳細を返す（歌手別TOPと同構造）。
    creator_type: 'lyricist' / 'composer' / 'year'
    戻り値: 曲単位のdictリスト
      {creator, creator_rank, total_score, song_id, song_title,
       artist_id, artist_name, score, rank_creator, order_creator}
    """
    if creator_type not in _CREATOR_COLUMNS:
        raise ValueError(f"Invalid creator_type: {creator_type}")
    col, is_numeric = _CREATOR_COLUMNS[creator_type]

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count >= top_n
    params.append(top_n)  # order_creator <= top_n

    sql = (
        _creator_filtered_cte(col, region_filter, is_numeric)
        + """
        ,
        ranked AS (
            SELECT
                f.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.creator
                    ORDER BY f.score DESC, UPPER(f.song_title)
                ) AS order_creator,
                RANK() OVER (
                    PARTITION BY f.creator
                    ORDER BY f.score DESC
                ) AS rank_creator
            FROM filtered f
        ),
        qualified AS (
            SELECT creator FROM counts WHERE song_count >= %s
        ),
        top_songs AS (
            SELECT r.*
            FROM ranked r
            JOIN qualified q ON r.creator = q.creator
            WHERE r.order_creator <= %s
        ),
        totals AS (
            SELECT creator, SUM(score) AS total_score FROM top_songs GROUP BY creator
        ),
        ranked_totals AS (
            SELECT
                creator,
                total_score,
                RANK() OVER (ORDER BY total_score DESC) AS creator_rank
            FROM totals
        )
        SELECT
            ts.creator,
            rt.creator_rank,
            rt.total_score,
            ts.song_id,
            ts.song_title,
            ts.artist_id,
            ts.artist_name,
            ts.score,
            ts.rank_creator,
            ts.order_creator
        FROM top_songs ts
        JOIN ranked_totals rt ON ts.creator = rt.creator
        ORDER BY rt.creator_rank, UPPER(ts.creator), ts.order_creator
    """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_creator_insufficient_songs(user_id, top_n, region_id, creator_type):
    """
    作詞者/作曲者/年ごとの曲数が top_n に満たないクリエイターの曲を返す（歌手別TOPの"その他"と同じ役割）。
    戻り値: 曲単位のdictリスト（scoreの降順で順位付け）
    """
    if creator_type not in _CREATOR_COLUMNS:
        raise ValueError(f"Invalid creator_type: {creator_type}")
    col, is_numeric = _CREATOR_COLUMNS[creator_type]

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count < top_n

    sql = (
        _creator_filtered_cte(col, region_filter, is_numeric)
        + """
        ,
        insufficient_creators AS (
            SELECT creator FROM counts WHERE song_count < %s
        )
        SELECT
            f.creator,
            f.song_id,
            f.song_title,
            f.artist_id,
            f.artist_name,
            f.score,
            RANK() OVER (ORDER BY f.score DESC) AS rank_within_insufficient
        FROM filtered f
        JOIN insufficient_creators ic ON f.creator = ic.creator
        ORDER BY f.score DESC, UPPER(f.artist_name), UPPER(f.song_title)
    """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_my_procedure(procname, *args):
    """
    任意のストアドプロシージャを呼び出し、結果セットを dict のリストで返す。
    （現在この関数は使われていないが、将来用に残してある）
    """
    with connection.cursor() as cursor:
        cursor.callproc(procname, args)
        columns = [col[0] for col in cursor.description]

        results = []
        while True:
            rows = cursor.fetchall()
            for row in rows:
                results.append(dict(zip(columns, row)))
            if not cursor.nextset():
                break

    return results


# ===== 歌手別ランキング（インライン CTE 版） =====
# 旧プロシージャ get_artist_top_n / get_artist_insufficient と
# rank_view / artist_song_counts_view への依存を排除して書き直したもの。

def _artist_filtered_cte(region_filter):
    """歌手ランキング用CTE: ユーザの評価済み非カバー曲＋歌手単位の曲数を計算"""
    return f"""
        WITH filtered AS (
            SELECT
                r.user_id,
                s.id AS song_id,
                s.title AS song_title,
                s.artist_id,
                a.name AS artist_name,
                a.region_id,
                r.score
            FROM songs_rating r
            JOIN songs_song s ON r.song_id = s.id
            JOIN songs_artist a ON s.artist_id = a.id
            WHERE r.user_id = %s
              AND s.is_cover = 0
              {region_filter}
        ),
        counts AS (
            SELECT artist_id, COUNT(*) AS song_count
            FROM filtered
            GROUP BY artist_id
        )
    """


def call_artist_song_top_n(user_id, top_n, region_id):
    """
    歌手別TOP（曲ごと詳細モード）。
    各歌手の上位N曲を、その歌手の合計点・順位とともに返す。
    歌手は「ユーザがその歌手の曲を top_n 曲以上評価済み」に限定。
    戻り値: 曲単位のdictリスト
      {song_id, song_title, artist_id, artist_name, region_id, score,
       order_artist, rank_artist, total_score, artist_rank}
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count >= top_n
    params.append(top_n)  # order_artist <= top_n

    sql = (
        _artist_filtered_cte(region_filter)
        + """
        ,
        ranked AS (
            SELECT
                f.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.artist_id
                    ORDER BY f.score DESC, UPPER(f.song_title)
                ) AS order_artist,
                RANK() OVER (
                    PARTITION BY f.artist_id
                    ORDER BY f.score DESC
                ) AS rank_artist
            FROM filtered f
        ),
        qualified AS (
            SELECT artist_id FROM counts WHERE song_count >= %s
        ),
        top_songs AS (
            SELECT r.*
            FROM ranked r
            JOIN qualified q ON r.artist_id = q.artist_id
            WHERE r.order_artist <= %s
        ),
        totals AS (
            SELECT artist_id, SUM(score) AS total_score
            FROM top_songs
            GROUP BY artist_id
        ),
        ranked_totals AS (
            SELECT
                artist_id,
                total_score,
                RANK() OVER (ORDER BY total_score DESC) AS artist_rank
            FROM totals
        )
        SELECT
            ts.song_id,
            ts.song_title,
            ts.artist_id,
            ts.artist_name,
            ts.region_id,
            ts.score,
            ts.order_artist,
            ts.rank_artist,
            rt.total_score,
            rt.artist_rank
        FROM top_songs ts
        JOIN ranked_totals rt ON ts.artist_id = rt.artist_id
        ORDER BY rt.artist_rank, UPPER(ts.artist_name), ts.order_artist
        """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_artist_top_n(user_id, top_n, region_id):
    """
    歌手別TOP（歌手集計モード）。歌手ごとに合計点と順位を返す。
    歌手は「ユーザがその歌手の曲を top_n 曲以上評価済み」に限定。
    戻り値: 歌手単位のdictリスト
      {artist_id, artist_name, region_id, total_score, artist_rank}
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count >= top_n
    params.append(top_n)  # order_artist <= top_n

    sql = (
        _artist_filtered_cte(region_filter)
        + """
        ,
        ranked AS (
            SELECT
                f.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.artist_id
                    ORDER BY f.score DESC, UPPER(f.song_title)
                ) AS order_artist
            FROM filtered f
        ),
        qualified AS (
            SELECT artist_id FROM counts WHERE song_count >= %s
        ),
        top_songs AS (
            SELECT r.*
            FROM ranked r
            JOIN qualified q ON r.artist_id = q.artist_id
            WHERE r.order_artist <= %s
        ),
        totals AS (
            SELECT
                artist_id,
                MAX(artist_name) AS artist_name,
                MAX(region_id) AS region_id,
                SUM(score) AS total_score
            FROM top_songs
            GROUP BY artist_id
        )
        SELECT
            artist_id,
            artist_name,
            region_id,
            total_score,
            RANK() OVER (ORDER BY total_score DESC) AS artist_rank
        FROM totals
        ORDER BY artist_rank, UPPER(artist_name)
        """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_artist_insufficient_songs(user_id, top_n, region_id):
    """
    歌手別TOPの「その他」枠：
    その歌手の評価済み曲数が top_n に満たない歌手について、
    各歌手の上位曲（order_artist <= top_n）をスコア順で並べて返す。
    戻り値: 曲単位のdictリスト
      {song_id, song_title, artist_id, artist_name, region_id, score,
       order_artist, rank_within_insufficient}
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # order_artist <= top_n
    params.append(top_n)  # song_count < top_n

    sql = (
        _artist_filtered_cte(region_filter)
        + """
        ,
        ranked AS (
            SELECT
                f.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.artist_id
                    ORDER BY f.score DESC, UPPER(f.song_title)
                ) AS order_artist
            FROM filtered f
        ),
        top_songs AS (
            SELECT * FROM ranked WHERE order_artist <= %s
        ),
        insufficient_songs AS (
            SELECT ts.*
            FROM top_songs ts
            JOIN counts c ON ts.artist_id = c.artist_id
            WHERE c.song_count < %s
        )
        SELECT
            song_id,
            song_title,
            artist_id,
            artist_name,
            region_id,
            score,
            order_artist,
            RANK() OVER (ORDER BY score DESC) AS rank_within_insufficient
        FROM insufficient_songs
        ORDER BY score DESC, UPPER(artist_name), UPPER(song_title)
        """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_artist_insufficient(user_id, top_n, region_id):
    """
    歌手集計モードの「その他」枠：
    その歌手の総曲数のうち、ユーザの評価済み曲数が top_n 未満で、かつ 1 曲以上ある歌手を返す。
    （現在は views.py 上では未使用だがインタフェース互換のため残す）
    戻り値: dictリスト  {id, name, total_songs, rated_count}
    """
    params = [user_id]
    region_filter = ""
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # rated_count < top_n

    sql = f"""
        SELECT
            a.id AS id,
            a.name AS name,
            COUNT(*) AS total_songs,
            SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) AS rated_count
        FROM songs_artist a
        INNER JOIN songs_song s ON a.id = s.artist_id
        LEFT JOIN (
            SELECT id, song_id FROM songs_rating WHERE user_id = %s
        ) r ON s.id = r.song_id
        WHERE s.is_cover = 0
          {region_filter}
        GROUP BY a.id, a.name
        HAVING SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) < %s
           AND SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) > 0
        ORDER BY rated_count DESC, total_songs DESC, UPPER(a.name)
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
