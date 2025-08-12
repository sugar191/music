DELIMITER $$

CREATE PROCEDURE get_artist_top_n(
    IN p_user_id INT,
    IN p_top_n INT,
    IN p_region_id INT,
    IN p_detail_mode BOOLEAN  -- TRUE=曲ごと詳細, FALSE=アーティスト集計
)
BEGIN
    IF p_detail_mode THEN
        -- 詳細モード（曲ごと）
        SELECT
            ts.*,
            a.total_score,
            a.artist_rank
        FROM (
            SELECT *
            FROM rank_view
            WHERE user_id = p_user_id
              AND order_artist <= p_top_n
              AND (p_region_id IS NULL OR region_id = p_region_id)
        ) ts
        JOIN (
            SELECT
                ts.user_id,
                ts.artist_id,
                ts.artist_name,
                ts.region_id,
                SUM(ts.score) AS total_score,
                RANK() OVER (PARTITION BY ts.user_id ORDER BY SUM(ts.score) DESC) AS artist_rank
            FROM rank_view ts
            JOIN artist_song_counts_view ascnt
              ON ts.user_id = ascnt.user_id
             AND ts.artist_id = ascnt.artist_id
            WHERE ts.user_id = p_user_id
              AND ts.order_artist <= p_top_n
              AND (p_region_id IS NULL OR ts.region_id = p_region_id)
              AND ascnt.song_count >= p_top_n
            GROUP BY ts.user_id, ts.artist_id, ts.artist_name, ts.region_id
        ) a
          ON ts.user_id = a.user_id
         AND ts.artist_id = a.artist_id
        ORDER BY ts.user_id, a.artist_rank, UPPER(a.artist_name), ts.order_artist;

    ELSE
        -- 集計モード（アーティスト単位）
        SELECT
            ts.user_id,
            ts.artist_id,
            ts.artist_name,
            ts.region_id,
            SUM(ts.score) AS total_score,
            RANK() OVER (PARTITION BY ts.user_id ORDER BY SUM(ts.score) DESC) AS artist_rank
        FROM rank_view ts
        JOIN artist_song_counts_view ascnt
          ON ts.user_id = ascnt.user_id
         AND ts.artist_id = ascnt.artist_id
        WHERE ts.user_id = p_user_id
          AND ts.order_artist <= p_top_n
          AND (p_region_id IS NULL OR ts.region_id = p_region_id)
          AND ascnt.song_count >= p_top_n
        GROUP BY ts.user_id, ts.artist_id, ts.artist_name, ts.region_id;
    END IF;
END $$

DELIMITER $$

CREATE PROCEDURE get_artist_insufficient(
    IN p_user_id INT,
    IN p_top_n INT,
    IN p_region_id INT,
    IN p_detail_mode BOOLEAN  -- TRUE=曲ごと詳細, FALSE=アーティスト集計
)
BEGIN
    IF p_detail_mode THEN
        -- 曲ごと詳細モード（現行仕様）
        WITH top_songs AS (
            SELECT *
            FROM rank_view
            WHERE user_id = p_user_id
              AND order_artist <= p_top_n
              AND (p_region_id IS NULL OR region_id = p_region_id)
        ),
        insufficient_artists_songs AS (
            SELECT
                ts.*
            FROM top_songs ts
            LEFT JOIN artist_song_counts_view ascnt
              ON ts.user_id = ascnt.user_id
             AND ts.artist_id = ascnt.artist_id
            WHERE ascnt.song_count < p_top_n OR ascnt.song_count IS NULL
        )
        SELECT
            iss.*,
            RANK() OVER (ORDER BY score DESC) AS rank_within_insufficient
        FROM insufficient_artists_songs iss
        ORDER BY score DESC, UPPER(artist_name), UPPER(song_title);

    ELSE
        -- アーティスト集計モード

        SELECT
				a.id,
				a.name,
				COUNT(1) AS total_songs,
				SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) AS rated_count
			FROM
				songs_artist a
			INNER JOIN
				songs_song s
			ON
				a.id = s.artist_id
			LEFT JOIN
				(SELECT * from songs_rating WHERE user_id = p_user_id) r
			ON
				s.id = r.song_id
			WHERE
				(a.region_id = p_region_id OR p_region_id IS NULL)
				AND
				s.is_cover = 0
			GROUP BY
				a.id,
				a.name
			HAVING
				SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) < p_top_n
				AND
				SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) > 0
			ORDER BY
				SUM(CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) DESC,
				COUNT(1) DESC,
				UPPER(a.name)
			;
    END IF;
END $$

DELIMITER ;
