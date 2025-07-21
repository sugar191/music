import os
import csv
from mutagen.easyid3 import EasyID3
from pathlib import Path

# パス定義
music_dir = Path(r"C:\Users\pawab\Music")
artist_csv_path = Path(r"C:\Users\pawab\Documents\音楽\Artist-2025-07-19.csv")
song_csv_path = Path(r"C:\Users\pawab\Documents\音楽\Song-2025-07-19.csv")
output_missing_path = Path(r"C:\Users\pawab\Documents\音楽\MissingSongs.csv")

# JPOP以外のアーティストも含めるかどうか
INCLUDE_NON_JPOP = False

# アーティストID -> 名前・region
artist_info = {}
with open(artist_csv_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        artist_info[row["id"]] = {
            "name": row["name"].strip(),
            "region": row["region"].strip(),
        }

# 登録済みの曲（title, artist_name）のセット
registered_songs = set()
song_rows = []

with open(song_csv_path, newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    for row in reader:
        artist_id = row["artist"]
        artist_data = artist_info.get(artist_id)
        if not artist_data:
            continue
        if not INCLUDE_NON_JPOP and artist_data["region"] != "1":
            continue

        title = row["title"].strip()
        artist_name = artist_data["name"]
        if artist_name and title:
            registered_songs.add((title, artist_name))
            song_rows.append(
                {"id": row["id"], "artist_name": artist_name, "title": title}
            )

# MP3ファイルからタグ情報を取得
existing_songs = set()
for mp3_file in music_dir.glob("*.mp3"):
    try:
        audio = EasyID3(mp3_file)
        title = audio.get("title", [""])[0].strip()
        artist = audio.get("artist", [""])[0].strip()
        if title and artist:
            existing_songs.add((title, artist))
    except Exception as e:
        print(f"{mp3_file.name}: タグ取得失敗 - {e}")

# 比較して存在しない曲を抽出
missing_songs = [
    row for row in song_rows if (row["title"], row["artist_name"]) not in existing_songs
]

# アーティスト名 → 曲名の順でソート
missing_songs.sort(key=lambda x: (x["artist_name"], x["title"]))

# 出力（列順：id, artist_name, title, YouTube）
with open(output_missing_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "artist_name", "title", "YouTube"])
    writer.writeheader()
    for song in missing_songs:
        artist_encoded = song["artist_name"].replace(" ", "+")
        title_encoded = song["title"].replace(" ", "+")
        youtube_url = f"https://www.youtube.com/results?search_query={artist_encoded}+{title_encoded}"
        writer.writerow(
            {
                "id": song["id"],
                "artist_name": song["artist_name"],
                "title": song["title"],
                "YouTube": youtube_url,
            }
        )


print(f"MissingSongs.csv を作成しました（{len(missing_songs)} 件）")
