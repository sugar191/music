import os, datetime, subprocess, shlex
from django.conf import settings
from django.http import (
    JsonResponse,
    FileResponse,
    HttpResponseBadRequest,
    HttpResponse,
    Http404,
)
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt

EXPORT_API_TOKEN = getattr(settings, "EXPORT_API_TOKEN", "put-a-long-random-token")
DB = settings.DATABASES["default"]
PA_DB_HOST = DB.get("HOST") or "127.0.0.1"
PA_DB_PORT = str(DB.get("PORT") or "3306")
PA_DB_NAME = DB.get("NAME")
PA_DB_USER = DB.get("USER")
PA_DB_PASS = DB.get("PASSWORD")  # ← ここは None の可能性もあるので後段で条件付き

DUMP_DIR = os.path.expanduser("~/dumps/music")


def _auth(request):
    tok = (
        request.headers.get("X-Export-Token")
        or request.POST.get("token")
        or request.GET.get("token")
    )
    return tok == EXPORT_API_TOKEN


def _dump_one(table: str, outfile: str):
    os.makedirs(DUMP_DIR, exist_ok=True)
    cmd = [
        "mysqldump",
        "-h",
        PA_DB_HOST,
        "-P",
        PA_DB_PORT,
        "-u",
        PA_DB_USER,
    ]
    # パスワードがある場合のみ付与（= None 対策）
    if PA_DB_PASS:
        cmd.append(f"--password={PA_DB_PASS}")

    cmd += [
        "--single-transaction",
        "--quick",
        "--skip-lock-tables",
        "--no-tablespaces",
        "--set-gtid-purged=OFF",
        "--no-create-info",
        "--skip-triggers",
        PA_DB_NAME,
        table,
    ]

    with open(outfile, "wb") as f:
        subprocess.run(cmd, check=True, stdout=f, stderr=subprocess.PIPE, timeout=600)


@csrf_exempt
@require_POST
def dump_tables(request):
    if not _auth(request):
        return HttpResponse(status=401)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    artist_path = os.path.join(DUMP_DIR, f"pa_artist_{ts}.sql")
    song_path = os.path.join(DUMP_DIR, f"pa_song_{ts}.sql")
    try:
        # 依存順（artist→song）
        _dump_one("songs_artist", artist_path)
        _dump_one("songs_song", song_path)
    except subprocess.CalledProcessError as e:
        return JsonResponse(
            {"ok": False, "stderr": e.stderr.decode("utf-8", "ignore")}, status=500
        )
    return JsonResponse({"ok": True, "files": [artist_path, song_path]})


@require_GET
def list_dumps(request):
    if not _auth(request):
        return HttpResponse(status=401)
    if not os.path.isdir(DUMP_DIR):
        return JsonResponse({"files": []})
    files = sorted([f for f in os.listdir(DUMP_DIR) if f.endswith(".sql")])
    return JsonResponse({"files": files})


@require_GET
def download_dump(request):
    if not _auth(request):
        return HttpResponse(status=401)
    name = request.GET.get("name")
    if not name or "/" in name or "\\" in name:
        return HttpResponseBadRequest("bad name")
    path = os.path.join(DUMP_DIR, name)
    if not os.path.isfile(path):
        raise Http404()
    resp = FileResponse(open(path, "rb"), content_type="application/sql")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp
