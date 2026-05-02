-- rank_view は廃止しました。
--   - 旧定義: ウィンドウ関数を含むビューだったため、MySQL 上は必ず TEMPTABLE
--     アルゴリズムで実体化され、user_id でフィルタする前に全行を計算していた。
--   - 置き換え: songs/services.py の call_song_ranking（user_id を CTE で先に絞る形）。
--   - DB 上のビューは migration 0018_drop_rankview で DROP 済み。
--   - reverse 用の DDL は 0018_drop_rankview.py に保持。

-- artist_song_counts_view は rank_view に依存していたため、現在は壊れた状態。
-- 参照元の create_procedure.sql のプロシージャ自体もアプリから呼ばれていないので
-- このファイルでは触らずに残す（将来クリーンアップ予定）。
