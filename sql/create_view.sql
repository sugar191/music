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

  -- ユーザー×アーティスト内の順位（従来どおり）
  RANK()       OVER (PARTITION BY r.user_id, a.id
                     ORDER BY r.score DESC)                  AS rank_artist,
  ROW_NUMBER() OVER (PARTITION BY r.user_id, a.id
                     ORDER BY r.score DESC, UPPER(s.title))  AS order_artist,

  -- ユーザー全体の順位（従来どおり）
  RANK()       OVER (PARTITION BY r.user_id
                     ORDER BY r.score DESC)                  AS rank_all,
  ROW_NUMBER() OVER (PARTITION BY r.user_id
                     ORDER BY r.score DESC, UPPER(a.name), UPPER(s.title)) AS order_all,

  -- ★追加：ユーザー×地域の順位（絞り込み表示用）
  RANK()       OVER (PARTITION BY r.user_id, a.region_id
                     ORDER BY r.score DESC)                  AS rank_region,
  ROW_NUMBER() OVER (PARTITION BY r.user_id, a.region_id
                     ORDER BY r.score DESC, UPPER(a.name), UPPER(s.title)) AS order_region

FROM songs_artist a
JOIN songs_song   s ON a.id = s.artist_id
JOIN songs_rating r ON s.id = r.song_id
WHERE s.is_cover = 0;
;

CREATE OR REPLACE VIEW artist_song_counts_view AS
SELECT
    user_id,
    artist_id,
    artist_name,
    region_id,
    COUNT(*) AS song_count
FROM rank_view
GROUP BY user_id, artist_id, artist_name, region_id;