from django.db import migrations, connection


def set_all_collations(apps, schema_editor):
    db_name = connection.settings_dict["NAME"]
    cursor = connection.cursor()

    # 1. データベース全体のデフォルト変更
    cursor.execute(
        f"""
        ALTER DATABASE `{db_name}`
        CHARACTER SET utf8mb4
        COLLATE utf8mb4_ja_0900_as_cs;
    """
    )

    # 2. 全テーブルの collation を変更
    cursor.execute("SHOW TABLES;")
    tables = [row[0] for row in cursor.fetchall()]
    for table in tables:
        cursor.execute(
            f"""
            ALTER TABLE `{table}`
            CONVERT TO CHARACTER SET utf8mb4
            COLLATE utf8mb4_ja_0900_as_cs;
        """
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "songs",
            "0006_artist_format_name_song_format_title_and_more",
        ),  # 例: ('songs', '0006_auto_20250811_1234')
    ]

    operations = [
        migrations.RunPython(set_all_collations, reverse_code=migrations.RunPython.noop)
    ]
