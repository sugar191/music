from django.db import connection


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
