from django.db import connection

# 作詞/作曲/年 ランキング用の対象カラム（SQLインジェクション対策のためホワイトリスト化）
# value: (DBカラム名, 数値カラムか)
_CREATOR_COLUMNS = {
    "lyricist": ("lyricist", False),
    "composer": ("composer", False),
    "year": ("year", True),
}


def _score_column(karaoke_mode):
    """
    ランキングの集計対象カラム名を返す。
    karaoke_mode=True ならカラオケ採点、False なら従来の点数。
    固定の2値しか返さないので SQL へ直接埋め込んでよい。
    """
    return "r.karaoke_score" if karaoke_mode else "r.score"


def _creator_filtered_cte(col, region_filter, is_numeric=False, karaoke_mode=False):
    """
    作詞/作曲/年ランキングの共通CTE: 評価済・非カバー・対象カラム入力済の曲を抽出。

    r.score IS NOT NULL は必須。Rating はカラオケ点数だけ登録される場合があり
    （API の update_score が score=NULL の行を作る）、除外しないと
    好み度が無い曲まで song_count に数えられて TOP{N} の成立条件が甘くなる。
    karaoke_mode のときは逆に「カラオケ採点が入っている曲」だけを対象にする。

    集計対象カラムは score という別名で返すため、この CTE を使う側の SQL は
    モードによらず変更不要。
    """
    # 数値カラム（year）は <> '' のチェックを外す（型エラー回避）
    empty_check = "" if is_numeric else f"AND s.{col} <> ''"
    score_col = _score_column(karaoke_mode)
    return f"""
        WITH filtered AS (
            SELECT
                r.user_id,
                s.{col} AS creator,
                s.id AS song_id,
                s.title AS song_title,
                s.artist_id,
                a.name AS artist_name,
                {score_col} AS score,
                a.region_id,
                s.lyricist AS lyricist,
                s.composer AS composer,
                s.year AS year
            FROM songs_rating r
            JOIN songs_song s ON r.song_id = s.id
            JOIN songs_artist a ON s.artist_id = a.id
            WHERE r.user_id = %s
              AND {score_col} IS NOT NULL
              AND s.is_cover = 0
              AND s.{col} IS NOT NULL
              {empty_check}
              {region_filter}
        ),
        counts AS (
            SELECT creator, COUNT(*) AS song_count FROM filtered GROUP BY creator
        )
    """


def call_creator_song_top_n(user_id, top_n, region_id, creator_type, karaoke_mode=False):
    """
    作詞者・作曲者・年ごとに、上位N曲の詳細を返す（歌手別TOPと同構造）。
    creator_type: 'lyricist' / 'composer' / 'year'
    karaoke_mode: True ならカラオケ採点で集計する
    戻り値: 曲単位のdictリスト
      {creator, creator_rank, total_score, song_id, song_title,
       artist_id, artist_name, score, rank_creator, order_creator,
       lyricist, composer, year}
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

    sql = _creator_filtered_cte(col, region_filter, is_numeric, karaoke_mode) + """
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
            ts.order_creator,
            ts.lyricist,
            ts.composer,
            ts.year
        FROM top_songs ts
        JOIN ranked_totals rt ON ts.creator = rt.creator
        ORDER BY rt.creator_rank, UPPER(ts.creator), ts.order_creator
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_creator_insufficient_songs(
    user_id, top_n, region_id, creator_type, karaoke_mode=False
):
    """
    作詞者/作曲者/年ごとの曲数が top_n に満たないクリエイターの曲を返す（歌手別TOPの"その他"と同じ役割）。
    karaoke_mode: True ならカラオケ採点で集計する
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

    sql = _creator_filtered_cte(col, region_filter, is_numeric, karaoke_mode) + """
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
            f.lyricist,
            f.composer,
            f.year,
            RANK() OVER (ORDER BY f.score DESC) AS rank_within_insufficient
        FROM filtered f
        JOIN insufficient_creators ic ON f.creator = ic.creator
        ORDER BY f.score DESC, UPPER(f.artist_name), UPPER(f.song_title)
    """

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


def _artist_filtered_cte(region_filter, karaoke_mode=False):
    """
    歌手ランキング用CTE: ユーザの評価済み非カバー曲＋歌手単位の曲数を計算。

    score IS NOT NULL / karaoke_mode については _creator_filtered_cte のコメントを参照。
    """
    score_col = _score_column(karaoke_mode)
    return f"""
        WITH filtered AS (
            SELECT
                r.user_id,
                s.id AS song_id,
                s.title AS song_title,
                s.artist_id,
                a.name AS artist_name,
                a.region_id,
                {score_col} AS score,
                s.lyricist AS lyricist,
                s.composer AS composer,
                s.year AS year
            FROM songs_rating r
            JOIN songs_song s ON r.song_id = s.id
            JOIN songs_artist a ON s.artist_id = a.id
            WHERE r.user_id = %s
              AND {score_col} IS NOT NULL
              AND s.is_cover = 0
              {region_filter}
        ),
        counts AS (
            SELECT artist_id, COUNT(*) AS song_count
            FROM filtered
            GROUP BY artist_id
        )
    """


def call_artist_song_top_n(user_id, top_n, region_id, karaoke_mode=False):
    """
    歌手別TOP（曲ごと詳細モード）。
    各歌手の上位N曲を、その歌手の合計点・順位とともに返す。
    歌手は「ユーザがその歌手の曲を top_n 曲以上評価済み」に限定。
    karaoke_mode: True ならカラオケ採点で集計する
    戻り値: 曲単位のdictリスト
      {song_id, song_title, artist_id, artist_name, region_id, score,
       order_artist, rank_artist, total_score, artist_rank,
       lyricist, composer, year}
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))
    params.append(top_n)  # song_count >= top_n
    params.append(top_n)  # order_artist <= top_n

    sql = _artist_filtered_cte(region_filter, karaoke_mode) + """
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
            rt.artist_rank,
            ts.lyricist,
            ts.composer,
            ts.year
        FROM top_songs ts
        JOIN ranked_totals rt ON ts.artist_id = rt.artist_id
        ORDER BY rt.artist_rank, UPPER(ts.artist_name), ts.order_artist
        """

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_artist_top_n(user_id, top_n, region_id):
    """
    歌手別TOP（歌手集計モード）。歌手ごとに合計点と順位を返す。
    （現在は call_artist_top_n_multi に置き換えられて未使用。単一 top_n だけ
      欲しいときの参照実装として残してある）
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

    sql = _artist_filtered_cte(region_filter) + """
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

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


TOP_NS = (5, 10, 15, 20)


def _validate_top_ns(top_ns):
    """SQLに直接埋め込むため、整数であることを保証する"""
    ns = [int(n) for n in top_ns]
    if not ns:
        raise ValueError("top_ns が空です")
    return ns


def call_artist_top_n_multi(user_id, region_id, top_ns=TOP_NS, karaoke_mode=False):
    """
    歌手ランキングを top_n 4種類ぶんまとめて1クエリで返す（歌手TOP / 歌手ランク用）。

    従来は call_artist_top_n を top_n ごとに4回呼んでいたが、
    重い部分（曲の順位付けウィンドウ関数）は top_n に依存しないため共有できる。

    戻り値: 歌手単位のdictリスト
      {artist_id, artist_name, region_id,
       total_5, rank_5, order_5, total_10, rank_10, order_10, ...}
      その top_n の条件（評価済み曲数 >= top_n）を満たさない歌手は
      total_N / rank_N / order_N が None になる。
      order_N は表示順（同点時の並びを従来のSQLと一致させるための通し番号）。
    """
    ns = _validate_top_ns(top_ns)

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))

    total_cols = ",\n            ".join(
        f"CASE WHEN c.song_count >= {n} "
        f"THEN SUM(CASE WHEN r.order_artist <= {n} THEN r.score END) END AS total_{n}"
        for n in ns
    )
    rank_cols = ",\n            ".join(
        f"""total_{n},
            CASE WHEN total_{n} IS NULL THEN NULL
                 ELSE RANK() OVER (ORDER BY total_{n} DESC) END AS rank_{n},
            CASE WHEN total_{n} IS NULL THEN NULL
                 ELSE ROW_NUMBER() OVER (ORDER BY total_{n} DESC, UPPER(artist_name)) END AS order_{n}"""
        for n in ns
    )

    sql = (
        _artist_filtered_cte(region_filter, karaoke_mode)
        + f"""
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
        totals AS (
            SELECT
                r.artist_id,
                MAX(r.artist_name) AS artist_name,
                MAX(r.region_id) AS region_id,
                {total_cols}
            FROM ranked r
            JOIN counts c ON r.artist_id = c.artist_id
            GROUP BY r.artist_id, c.song_count
        )
        SELECT
            artist_id,
            artist_name,
            region_id,
            {rank_cols}
        FROM totals
        ORDER BY UPPER(artist_name)
        """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_creator_top_n_multi(
    user_id, region_id, creator_type, top_ns=TOP_NS, karaoke_mode=False
):
    """
    作詞者/作曲者/年ランキングを top_n 4種類ぶんまとめて1クエリで返す
    （作詞・作曲・年のTOP / ランク画面用）。

    戻り値: クリエイター単位のdictリスト
      {creator, total_5, rank_5, total_10, rank_10, ...}
      条件を満たさない top_n は total_N / rank_N が None。
    """
    if creator_type not in _CREATOR_COLUMNS:
        raise ValueError(f"Invalid creator_type: {creator_type}")
    col, is_numeric = _CREATOR_COLUMNS[creator_type]
    ns = _validate_top_ns(top_ns)

    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))

    total_cols = ",\n            ".join(
        f"CASE WHEN c.song_count >= {n} "
        f"THEN SUM(CASE WHEN r.order_creator <= {n} THEN r.score END) END AS total_{n}"
        for n in ns
    )
    rank_cols = ",\n            ".join(
        f"""total_{n},
            CASE WHEN total_{n} IS NULL THEN NULL
                 ELSE RANK() OVER (ORDER BY total_{n} DESC) END AS rank_{n}"""
        for n in ns
    )

    sql = (
        _creator_filtered_cte(col, region_filter, is_numeric, karaoke_mode)
        + f"""
        ,
        ranked AS (
            SELECT
                f.*,
                ROW_NUMBER() OVER (
                    PARTITION BY f.creator
                    ORDER BY f.score DESC, UPPER(f.song_title)
                ) AS order_creator
            FROM filtered f
        ),
        totals AS (
            SELECT
                r.creator,
                {total_cols}
            FROM ranked r
            JOIN counts c ON r.creator = c.creator
            GROUP BY r.creator, c.song_count
        )
        SELECT
            creator,
            {rank_cols}
        FROM totals
        """
    )

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def call_artist_insufficient_songs(user_id, top_n, region_id, karaoke_mode=False):
    """
    歌手別TOPの「その他」枠：
    その歌手の評価済み曲数が top_n に満たない歌手について、
    各歌手の上位曲（order_artist <= top_n）をスコア順で並べて返す。
    karaoke_mode: True ならカラオケ採点で集計する
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

    sql = _artist_filtered_cte(region_filter, karaoke_mode) + """
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
            lyricist,
            composer,
            year,
            RANK() OVER (ORDER BY score DESC) AS rank_within_insufficient
        FROM insufficient_songs
        ORDER BY score DESC, UPPER(artist_name), UPPER(song_title)
        """

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def count_song_ranking(user_id, region_id):
    """
    全曲ランキングの総件数（「もっと見る」の残件数表示・打ち切り判定用）。
    call_song_ranking と同じ抽出条件で COUNT だけを取る軽量クエリ。
    ※ 条件を変えたら call_song_ranking 側も必ず合わせること。
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))

    sql = f"""
        SELECT COUNT(*)
        FROM songs_rating r
        JOIN songs_song s ON r.song_id = s.id
        JOIN songs_artist a ON s.artist_id = a.id
        WHERE r.user_id = %s
          AND r.score IS NOT NULL
          AND s.is_cover = 0
          {region_filter}
    """

    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def call_song_ranking(user_id, region_id, offset=0, limit=None):
    """
    全曲ランキング（旧 rank_view 置き換え）。
    region_id 指定時はその地域内のランキング、未指定時は全体ランキング。
    is_cover = 0 かつ好み度が入力済み（score IS NOT NULL）の曲のみ対象。
    user_id を CTE 内で先に絞り込むため、ウィンドウ関数の対象行数を最小化できる。

    limit を指定すると display_order 順の一部だけを返す（「もっと見る」用）。
    順位はウィンドウ関数で全件に対して計算してから切り出すため、
    offset を進めても順位番号は通しのまま連続する。

    戻り値: 曲単位のdictリスト
      {display_rank, display_order, artist_id, artist_name,
       song_id, song_title, score}
    """
    region_filter = ""
    params = [user_id]
    if region_id:
        region_filter = "AND a.region_id = %s"
        params.append(int(region_id))

    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT %s OFFSET %s"

    sql = f"""
        WITH filtered AS (
            SELECT
                s.id AS song_id,
                s.title AS song_title,
                s.artist_id,
                a.name AS artist_name,
                a.region_id,
                r.score,
                s.lyricist AS lyricist,
                s.composer AS composer,
                s.year AS year,
                r.karaoke_score
            FROM songs_rating r
            JOIN songs_song s ON r.song_id = s.id
            JOIN songs_artist a ON s.artist_id = a.id
            WHERE r.user_id = %s
              AND r.score IS NOT NULL
              AND s.is_cover = 0
              {region_filter}
        )
        SELECT
            song_id,
            song_title,
            artist_id,
            artist_name,
            score,
            RANK() OVER (
                ORDER BY score DESC
            ) AS display_rank,
            ROW_NUMBER() OVER (
                ORDER BY score DESC, UPPER(artist_name), UPPER(song_title)
            ) AS display_order,
            lyricist,
            composer,
            year,
            karaoke_score
        FROM filtered
        ORDER BY display_order
        {limit_clause}
    """

    if limit is not None:
        params.append(int(limit))
        params.append(int(offset))

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
