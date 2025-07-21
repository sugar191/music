import os
import csv
from mutagen.easyid3 import EasyID3  # pip install mutagen
from pathlib import Path

# パス定義
music_dir = Path(r"C:\Users\pawab\Music")
artist_csv_path = Path(r"C:\Users\pawab\Documents\音楽\Artist-2025-07-19.csv")
song_csv_path = Path(r"C:\Users\pawab\Documents\音楽\Song-2025-07-19.csv")

# 除外したいアルバム名のキーワードリスト（部分一致）
album_exclude_keywords = ["SEKAI ALBUM", "Adoの歌ってみたアルバム", "Fall Apart"]

# 条件②：特定のアーティスト＆アルバム空欄の組み合わせ除外（完全一致）
complex_exclude_conditions = [
    {"artist": "Ado", "album": ""},
    {"artist": "Leo/need", "album": ""},
]


# 既存データの読み込み
def load_existing_artists(filepath):
    artists = {}
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artists[row["name"]] = {"id": row["id"], "region": row["region"]}
    return artists


def load_existing_songs(filepath):
    songs = set()
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            songs.add((row["title"], row["artist"]))
    return songs


existing_artists = load_existing_artists(artist_csv_path)
existing_songs = load_existing_songs(song_csv_path)

# 新規検出用リスト
new_artists = {}
new_songs = []

# IDカウンタの初期値
next_artist_id = max(int(artist["id"]) for artist in existing_artists.values()) + 1
next_song_id = (
    max(
        int(row[0])
        for row in csv.reader(open(song_csv_path, encoding="utf-8"))
        if row[0].isdigit()
    )
    + 1
)

# MP3読み取り
for mp3_file in music_dir.glob("*.mp3"):
    try:
        audio = EasyID3(mp3_file)
        title = audio.get("title", [""])[0].strip()
        artist = audio.get("artist", [""])[0].strip()
        album = audio.get("album", [""])[0].strip()

        # 情報不足はスキップ
        if not title or not artist:
            continue

        # アルバム名に除外キーワードが含まれていればスキップ
        if any(keyword in album for keyword in album_exclude_keywords):
            continue

        # 除外対象かどうかをフラグで判定
        is_excluded = False
        for condition in complex_exclude_conditions:
            if artist == condition["artist"].strip():
                target_album = condition["album"].strip()
                if target_album == "" and album == "":
                    is_excluded = True
                    break
                elif target_album != "" and album == target_album:
                    is_excluded = True
                    break

        if is_excluded:
            continue

        # 新規アーティスト
        if artist not in existing_artists and artist not in new_artists:
            new_artists[artist] = {"id": str(next_artist_id), "region": "1"}
            next_artist_id += 1

        artist_id = existing_artists.get(artist, new_artists.get(artist))["id"]
        if (title, artist_id) not in existing_songs:
            new_songs.append(
                {"id": str(next_song_id), "title": title, "artist": artist_id}
            )
            next_song_id += 1

    except Exception as e:
        print(f"{mp3_file.name}: 読み込み失敗 - {e}")

# 出力ファイルの保存
output_artist_path = artist_csv_path.parent / "Artist.csv"
output_song_path = song_csv_path.parent / "Song.csv"

# 新規アーティスト
with open(output_artist_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "name", "region"])
    writer.writeheader()
    for name, info in new_artists.items():
        writer.writerow({"id": info["id"], "name": name, "region": info["region"]})

# 新規曲
with open(output_song_path, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=["id", "title", "artist"])
    writer.writeheader()
    for song in new_songs:
        writer.writerow(song)

print("Artist.csv と Song.csv を作成しました。")
