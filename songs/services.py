from django.db import connection


# 作詞/作曲ランキング用の対象カラム（SQLインジェクション対策のためホワイトリスト化）
_CREATOR_COLUMNS = {
    "lyricist": "lyricist",
    "composer": "composer",
}


def _creator_filtered_cte(col, region_filter):
    """作詞/作曲ランキングの共通CTE: 評価済・非カバー・クリエイター入力済の曲を抽出"""
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
              AND s.{col} <> ''
              {region_filter}
        ),
        counts AS (
            SELECT creator, COUNT(*) AS song_count FROM filtered GROUP BY creator
        )
    """


def call_creator_song_top_n(user_id, top_n, region_id, creator_type):
    """
    作詞者または作曲者ごとに、上位N曲の詳細を返す（歌手別TOPと同構造）。
    creator_type: 'lyricist' または 'composer'
    戻り値: 曲単位のdictリスト
      {creator, creator_rank, total_score, song_id, song_title,
       artist_id, artist_name, score, rank_creator, order_creator}
    """
    if creator_type not in _CREATOR_COLUMNS:
        raise ValueError(f"Invalid creator_type: {creator_type}")
    col = _CREATOR_COLUMNS[creator_type]

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count >= top_n
    params.append(top_n)  # order_creator <= top_n

    sql = (
        _creator_filtered_cte(col, region_filter)
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
    作詞者/作曲者ごとの曲数が top_n に満たないクリエイターの曲を返す（歌手別TOPの"その他"と同じ役割）。
    戻り値: 曲単位のdictリスト（scoreの降順で順位付け）
    """
    if creator_type not in _CREATOR_COLUMNS:
        raise ValueError(f"Invalid creator_type: {creator_type}")
    col = _CREATOR_COLUMNS[creator_type]

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count < top_n

    sql = (
        _creator_filtered_cte(col, region_filter)
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
    任意のストアドプロシージャを呼び出し、
    結果セットを dict のリストで返す。
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


# 使い方
def call_artist_song_top_n(user_id, top_n, region_id):
    return call_my_procedure("get_artist_top_n", user_id, top_n, region_id, True)


def call_artist_top_n(user_id, top_n, region_id):
    return call_my_procedure("get_artist_top_n", user_id, top_n, region_id, False)


def call_artist_insufficient_songs(user_id, top_n, region_id):
    return call_my_procedure("get_artist_insufficient", user_id, top_n, region_id, True)


def call_artist_insufficient(user_id, top_n, region_id):
    return call_my_procedure(
        "get_artist_insufficient", user_id, top_n, region_id, False
    )
