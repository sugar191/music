"""
外部API（MusicBrainz → VocaDB → iTunes）から作詞・作曲・年を取得する。

tools/fetch/fetch_song_credits.py（デプロイ対象外のCLI）から、検索ロジック部分だけを
アプリ用に写したもの。CSV読込・SQL出力・進捗管理・pandas 依存は持ってこない。
ロジックを直したときは tools 側とズレないよう両方に反映すること。

画面からは fetch_credits_for_song() を呼ぶ。外部APIのレート制限を守るため
sleep を挟むので、1曲で数秒〜数十秒かかる。
"""

import math
import re
import sys
import time

import jaconv
import musicbrainzngs
import requests

# ----------------------------------------------------------- 設定
CONTACT_EMAIL = "pawaburo@gmail.com"
APP_NAME = "song-credit-fetcher"
APP_VERSION = "0.1"

VOCADB_API = "https://vocadb.net/api"
SLEEP_VOCADB = 0.4   # VocaDB は明文化されたレート制限はないが礼儀として
SLEEP_MB     = 1.1   # MusicBrainz は 1 req/sec が公式上限

TABLE_NAME = "songs_song"

USER_AGENT = f"{APP_NAME}/{APP_VERSION} ( {CONTACT_EMAIL} )"

musicbrainzngs.set_useragent(APP_NAME, APP_VERSION, CONTACT_EMAIL)
musicbrainzngs.set_rate_limit(limit_or_interval=SLEEP_MB, new_requests=1)


# ----------------------------------------------------------- ユーティリティ
def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    s = str(v).strip()
    return s == "" or s.lower() in ("nan", "none")


def norm(s) -> str:
    """マッチング用に正規化: 全角→半角、ひらがな→カタカナ、小文字化、空白圧縮。"""
    if is_blank(s):
        return ""
    s = str(s).strip()
    s = jaconv.z2h(s, kana=False, ascii=True, digit=True)
    s = jaconv.hira2kata(s)
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def extract_year(v):
    if not v:
        return None
    m = re.search(r"(19|20)\d{2}", str(v))
    return int(m.group(0)) if m else None


def dedupe(xs):
    seen, out = set(), []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def join_names(xs):
    return "、".join(dedupe(xs))


# ----------------------------------------------------------- VocaDB
DEBUG = False  # --debug で True に


def _credit_names_for(item) -> list:
    """
    item の「非ボーカリスト」クレジットから候補名を集める。
    Producer タグの揺れ (Producer/Other/空) に耐性を持たせるため、
    categories が Vocalist/Subject 以外のもの（Producer・Circle・Band・Other 等）
    と Support フラグ無しのものをすべて拾う。
    additionalNames (旧名/別名) は意図しないマッチ (例: 米津玄師/Lemon が
    ハチ経由でヒット) を引き起こすので照合には使わない。
    """
    names = []
    for a in (item.get("artists") or []):
        cats = (a.get("categories") or "").lower()
        if cats in ("vocalist", "subject"):
            continue
        if a.get("isSupport"):
            continue
        names.append(a.get("name") or "")
        artist_obj = a.get("artist") or {}
        names.append(artist_obj.get("name") or "")
    return [norm(n) for n in names if n]


def _artist_matches(artist_n: str, candidate_names: list) -> bool:
    """artist_n が candidate のどれかに一致 (substring は最小長3で誤マッチ防止)。"""
    for p in candidate_names:
        if not p:
            continue
        if artist_n == p:
            return True
        if len(artist_n) >= 3 and artist_n in p:
            return True
        if len(p) >= 3 and p in artist_n:
            return True
    return False


def vocadb_lookup(artist: str, title: str) -> dict:
    """
    VocaDB から作詞・作曲・年を取得 (厳格マッチ版)。
    返り値の `_meta` に診断情報 {candidates, title_hit, artist_hit, other_artists} を必ず含む。
    other_artists: 曲名は当たったがユーザーartist と一致しなかった候補の artistString リスト。
    """
    meta = {"title_hit": False, "artist_hit": False, "both_hit": False,
            "other_artists": []}
    try:
        r = requests.get(
            f"{VOCADB_API}/songs",
            params={
                "query": title,
                "fields": "Artists",
                "maxResults": 50,
                "lang": "Default",
                "songTypes": "Original",
            },
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        items = r.json().get("items", [])
    except Exception as e:
        time.sleep(SLEEP_VOCADB)
        return {"_error": f"vocadb: {e}", "_meta": meta}
    time.sleep(SLEEP_VOCADB)

    if not items:
        if DEBUG:
            print(f"  [debug-vocadb] {artist}/{title}: 0 items", file=sys.stderr)
        return {"_meta": meta}

    artist_n = norm(artist)
    title_n = norm(title)
    if not artist_n or not title_n:
        return {"_meta": meta}

    # 個別に title_hit / artist_hit / both_hit / other_artists を計測
    for it in items:
        names = (norm(it.get("defaultName", "")), norm(it.get("name", "")))
        title_match = title_n in names
        artist_match = _artist_matches(artist_n, _credit_names_for(it))
        if title_match:
            meta["title_hit"] = True
        if artist_match:
            meta["artist_hit"] = True
        if title_match and artist_match:
            meta["both_hit"] = True
        if title_match and not artist_match:
            astr = it.get("artistString") or ""
            if astr:
                meta["other_artists"].append(astr)

    matched = []
    for it in items:
        names = (norm(it.get("defaultName", "")), norm(it.get("name", "")))
        if title_n not in names:
            continue
        credits = _credit_names_for(it)
        if not _artist_matches(artist_n, credits):
            continue
        matched.append(it)

    if DEBUG:
        print(f"  [debug-vocadb] {artist}/{title}: {len(items)} items, "
              f"{len(matched)} matched", file=sys.stderr)
        for it in matched[:3]:
            print(
                f"    matched: defaultName={it.get('defaultName')!r} "
                f"artistString={it.get('artistString')!r} "
                f"publishDate={it.get('publishDate')} "
                f"credits={_credit_names_for(it)}",
                file=sys.stderr,
            )
        if items and not matched:
            for it in items[:5]:
                print(
                    f"    rejected: defaultName={it.get('defaultName')!r} "
                    f"artistString={it.get('artistString')!r} "
                    f"songType={it.get('songType')} "
                    f"credits={_credit_names_for(it)}",
                    file=sys.stderr,
                )

    if not matched:
        return {"_meta": meta}

    # publishDate 昇順 (最古=原曲)
    chosen = sorted(matched, key=lambda it: it.get("publishDate") or "9999-99-99")[0]

    out = {"_meta": meta}
    year = extract_year(chosen.get("publishDate"))
    if year:
        out["year"] = year

    # ユーザーartistとマッチしたproducerの出力名はユーザーartistに揃える
    # (substring match などで credits の表記が揺れた場合の正規化用)
    # additionalNames は照合に使わない: 米津玄師=ハチ のような旧名経由の
    # 意図しないマッチを防ぐため
    def _names_of(a):
        return [norm(n) for n in [
            a.get("name") or "",
            (a.get("artist") or {}).get("name") or "",
        ] if n]

    # 候補クレジットを収集
    raw_credits = []
    for a in (chosen.get("artists") or []):
        if a.get("isSupport"):
            continue
        cats = (a.get("categories") or "").lower()
        if cats in ("vocalist", "subject"):
            continue
        roles = (a.get("effectiveRoles") or "").lower()
        if not roles:
            roles = (a.get("roles") or "").lower()
        vocadb_name = a.get("name") or (a.get("artist") or {}).get("name") or ""
        if not vocadb_name:
            continue
        is_user_match = _artist_matches(artist_n, _names_of(a))
        display = artist if is_user_match else vocadb_name
        raw_credits.append((display, roles, is_user_match))

    # 千本桜=黒うさP+三重の人 問題対策: effectiveRoles=Default が複数いる場合、
    # ユーザーartist一致のものだけ採用 (バンド版アレンジ等の混入を防ぐ)
    default_credits = [c for c in raw_credits if "default" in c[1] or c[1] == ""]
    if len(default_credits) > 1:
        matched = [c for c in default_credits if c[2]]
        if matched:
            default_credits = matched
    explicit_credits = [c for c in raw_credits
                        if not ("default" in c[1] or c[1] == "")]

    composers, lyricists = [], []
    for display, roles, _ in default_credits:
        composers.append(display)
        lyricists.append(display)
    for display, roles, _ in explicit_credits:
        # Arranger は composer から除外 (編曲≠作曲)
        if "composer" in roles:
            composers.append(display)
        if "lyricist" in roles:
            lyricists.append(display)

    if composers:
        out["composer"] = join_names(composers)
    if lyricists:
        out["lyricist"] = join_names(lyricists)
    return out


# ----------------------------------------------------------- MusicBrainz
def _mb_artist_credit_text(ac_list) -> str:
    parts = []
    for ac in ac_list or []:
        if isinstance(ac, dict):
            name = (ac.get("artist") or {}).get("name", "") or ac.get("name", "")
            parts.append(name)
        elif isinstance(ac, str):
            parts.append(ac)
    return " ".join(parts)


def _mb_search(title: str, artist=None, limit=25):
    """MB recording search. artist=None で title-only 検索。"""
    try:
        kwargs = {"recording": title, "limit": limit}
        if artist:
            kwargs["artist"] = artist
        res = musicbrainzngs.search_recordings(**kwargs)
    except Exception as e:
        return None, str(e)
    return res.get("recording-list", []), None


def _pick_mb_candidate(candidates, artist_n, title_n):
    """信頼できる候補を選ぶ。タイトル+アーティスト両方が(部分)一致するものだけ。"""
    # 1) タイトル完全一致 + アーティスト一致
    for c in candidates:
        ct = norm(c.get("title", ""))
        ca = norm(_mb_artist_credit_text(c.get("artist-credit")))
        if ct == title_n and (artist_n in ca or (len(ca) >= 3 and ca in artist_n)):
            return c
    # 2) タイトル部分一致 + アーティスト一致
    for c in candidates:
        ct = norm(c.get("title", ""))
        ca = norm(_mb_artist_credit_text(c.get("artist-credit")))
        title_ok = ct == title_n or (len(title_n) >= 2 and title_n in ct) \
                   or (len(ct) >= 2 and ct in title_n)
        artist_ok = artist_n in ca or (len(ca) >= 3 and ca in artist_n)
        if title_ok and artist_ok:
            return c
    return None  # 信頼できる候補なし


def _mb_compute_hits(candidates, artist_n, title_n):
    """候補リストから title_hit / artist_hit / both_hit / other_artists を計算。
    both_hit: 同一候補で title と artist が両方一致したものが 1 件以上ある。
    other_artists: title 一致だが artist 不一致のもののクレジット表示。"""
    title_hit = artist_hit = both_hit = False
    others = []
    for c in candidates or []:
        ct = norm(c.get("title", ""))
        ca_raw = _mb_artist_credit_text(c.get("artist-credit"))
        ca = norm(ca_raw)
        title_match = (ct == title_n or (len(title_n) >= 2 and title_n in ct)
                       or (len(ct) >= 2 and ct in title_n))
        artist_match = (artist_n in ca or (len(ca) >= 3 and ca in artist_n))
        if title_match: title_hit = True
        if artist_match: artist_hit = True
        if title_match and artist_match: both_hit = True
        if title_match and not artist_match and ca_raw:
            others.append(ca_raw)
    return title_hit, artist_hit, both_hit, others


def mb_lookup(artist: str, title: str) -> dict:
    """MusicBrainz から作詞・作曲・年を取得。
    `_meta` に診断情報必須。"""
    meta = {"title_hit": False, "artist_hit": False, "both_hit": False,
            "other_artists": []}
    artist_n = norm(artist)
    title_n = norm(title)

    candidates, err = _mb_search(title, artist=artist, limit=25)
    if err:
        return {"_error": f"mb_search: {err}", "_meta": meta}

    candidates = candidates or []
    th, ah, bh, oa = _mb_compute_hits(candidates, artist_n, title_n)
    meta["title_hit"] = meta["title_hit"] or th
    meta["artist_hit"] = meta["artist_hit"] or ah
    meta["both_hit"] = meta["both_hit"] or bh
    meta["other_artists"].extend(oa)

    chosen = _pick_mb_candidate(candidates, artist_n, title_n)

    # フォールバック: title-only 再検索
    candidates2 = []
    if chosen is None:
        candidates2, err2 = _mb_search(title, artist=None, limit=25)
        if err2:
            return {"_error": f"mb_search2: {err2}", "_meta": meta}
        candidates2 = candidates2 or []
        th2, ah2, bh2, oa2 = _mb_compute_hits(candidates2, artist_n, title_n)
        meta["title_hit"] = meta["title_hit"] or th2
        meta["artist_hit"] = meta["artist_hit"] or ah2
        meta["both_hit"] = meta["both_hit"] or bh2
        meta["other_artists"].extend(oa2)
        chosen = _pick_mb_candidate(candidates2, artist_n, title_n)

    if chosen is None:
        if DEBUG:
            print(f"  [debug-mb] {artist}/{title}: no confident match "
                  f"(1st_cands={len(candidates)}, 2nd_cands={len(candidates2)})",
                  file=sys.stderr)
            for c in candidates[:3]:
                ca = _mb_artist_credit_text(c.get("artist-credit"))
                print(f"    cand: title={c.get('title')!r} artist={ca!r} "
                      f"date={c.get('first-release-date')}", file=sys.stderr)
        return {"_meta": meta}
    elif DEBUG:
        ca = _mb_artist_credit_text(chosen.get("artist-credit"))
        print(f"  [debug-mb] {artist}/{title}: chose title={chosen.get('title')!r} "
              f"artist={ca!r} date={chosen.get('first-release-date')}",
              file=sys.stderr)

    out = {"_meta": meta}
    year = extract_year(chosen.get("first-release-date"))
    if year:
        out["year"] = year

    # work へ辿って composer / lyricist を取得
    try:
        full = musicbrainzngs.get_recording_by_id(
            chosen["id"], includes=["work-rels"]
        )
    except Exception as e:
        out["_partial"] = f"recording lookup: {e}"
        return out

    rec = full.get("recording", {})
    work_rels = [
        r for r in (rec.get("work-relation-list") or [])
        if (r.get("type") or "").lower() == "performance"
    ]
    if not work_rels:
        return out

    # 通常 1 曲につき work は 1 つ
    work_id = work_rels[0]["work"]["id"]
    try:
        wfull = musicbrainzngs.get_work_by_id(work_id, includes=["artist-rels"])
    except Exception as e:
        out["_partial"] = f"work lookup: {e}"
        return out

    work = wfull.get("work", {})
    composers, lyricists = [], []
    for ar in (work.get("artist-relation-list") or []):
        rel_type = (ar.get("type") or "").lower()
        name = (ar.get("artist") or {}).get("name", "")
        if not name:
            continue
        if rel_type == "composer":
            composers.append(name)
        elif rel_type == "lyricist":
            lyricists.append(name)
        elif rel_type == "writer":
            composers.append(name)
            lyricists.append(name)

    if composers:
        out["composer"] = join_names(composers)
    if lyricists:
        out["lyricist"] = join_names(lyricists)
    return out


# ----------------------------------------------------------- iTunes Search API
ITUNES_API = "https://itunes.apple.com/search"
SLEEP_ITUNES = 3.0  # iTunes は ~20 req/min


def itunes_lookup(artist: str, title: str) -> dict:
    """iTunes Search API (無料・APIキー不要) から release 年と composer を取得。
    iTunes には lyricist フィールドが無いので作詞は取れない。
    `_meta` に診断情報必須。"""
    meta = {"title_hit": False, "artist_hit": False, "both_hit": False,
            "other_artists": []}
    try:
        r = requests.get(
            ITUNES_API,
            params={
                "term": f"{artist} {title}",
                "country": "jp",
                "entity": "song",
                "media": "music",
                "limit": 25,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception as e:
        time.sleep(SLEEP_ITUNES)
        return {"_error": f"itunes: {e}", "_meta": meta}
    time.sleep(SLEEP_ITUNES)

    if not results:
        if DEBUG:
            print(f"  [debug-itunes] {artist}/{title}: 0 results", file=sys.stderr)
        return {"_meta": meta}

    artist_n = norm(artist)
    title_n = norm(title)
    matched = []
    for it in results:
        tn = norm(it.get("trackName", ""))
        an = norm(it.get("artistName", ""))
        title_ok = (tn == title_n
                    or (len(title_n) >= 2 and title_n in tn)
                    or (len(tn) >= 2 and tn in title_n))
        artist_ok = (artist_n in an
                     or (len(an) >= 3 and an in artist_n))
        if title_ok:
            meta["title_hit"] = True
        if artist_ok:
            meta["artist_hit"] = True
        if title_ok and artist_ok:
            meta["both_hit"] = True
        if title_ok and not artist_ok:
            an_raw = it.get("artistName") or ""
            if an_raw:
                meta["other_artists"].append(an_raw)
        if title_ok and artist_ok:
            matched.append(it)

    if DEBUG:
        print(f"  [debug-itunes] {artist}/{title}: {len(results)} results, "
              f"{len(matched)} matched", file=sys.stderr)
        for it in matched[:2]:
            print(f"    matched: trackName={it.get('trackName')!r} "
                  f"artistName={it.get('artistName')!r} "
                  f"releaseDate={it.get('releaseDate')}", file=sys.stderr)

    if not matched:
        return {"_meta": meta}

    chosen = sorted(matched, key=lambda r: r.get("releaseDate") or "9999")[0]
    out = {"_meta": meta}
    year = extract_year(chosen.get("releaseDate"))
    if year:
        out["year"] = year
    composer = (chosen.get("composerName") or "").strip()
    if composer:
        out["composer"] = composer
    return out


# ----------------------------------------------------------- 1 行処理
FIELDS = ("lyricist", "composer", "year")

# alias_names / alias_format_names の区切り文字。
# アーティスト名にカンマが入りうるのでカンマは使えない。
NAME_SEP = "|"

# 1曲あたりに試すアーティスト表記の最大数。
# 表記を増やすほど当たる可能性は上がるが、ヒットしなかった曲では
# その分だけ API 呼び出しが増える (MusicBrainz は 1req/sec 制限)。
# --max-artist-forms で変更できる。
MAX_ARTIST_FORMS = 4


def _split_names(value):
    """パイプ区切りの複数名を配列にする。

    区切りに | を使うのは、アーティスト名にカンマが入りうるため
    (例: 「大木英夫,津山洋子」)。
    """
    return [p.strip() for p in (value or "").split(NAME_SEP) if p.strip()]


def _dedupe_keep_order(values):
    """大文字小文字だけ違うものを同一視して重複を落とす (順序は保つ)。

    format_* は normalize() で小文字化されているので、英字名では
    name と format_name が「大文字小文字だけの違い」になることが多い
    (The Beatles / the beatles)。外部APIの検索は大小を区別しないため、
    その差分で API を余分に叩くのは無駄。

    一方、NFKC で開かれた違いは別物として残す必要がある
    (Tommy february⁶ → tommy february6、40㍍P → 40mP)。
    なので normalize() ではなく casefold() だけで判定する。
    """
    out, seen = [], set()
    for v in values:
        v = (v or "").strip()
        if not v:
            continue
        key = v.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def _get_search_pairs(row: dict):
    """(artist, title) の試行ペアを優先度順に返す。

    アーティスト表記は DB の3テーブルに対応して次の順に集める:
      1. credit_name        その曲の名義。リリース上の表記そのものなので最優先
                            (サザン名義の曲と桑田佳祐名義の曲で変わる)
      2. name (or artist)   Artist 本体の名前
      3. alias_names        別表記 (The Beatles / 鄧麗君 など。| 区切りで複数可)
      4. 上記の正規化形     credit_format_name → format_name → alias_format_names

    正規化形を後ろに回すのは、多くの場合それが元の名前と大文字小文字しか
    違わず、_dedupe_keep_order で落ちるため。落ちずに残るのは NFKC で
    記号が開かれたケースで、そこは実際に当たることがある。

    タイトルは title → format_title の順 (従来どおり)。
    """
    artists = _dedupe_keep_order(
        [
            (row.get("credit_name") or "").strip(),
            (row.get("name") or row.get("artist") or "").strip(),
            *_split_names(row.get("alias_names")),
            (row.get("credit_format_name") or "").strip(),
            (row.get("format_name") or "").strip(),
            *_split_names(row.get("alias_format_names")),
        ]
    )[:MAX_ARTIST_FORMS]

    titles = _dedupe_keep_order(
        [
            (row.get("title") or "").strip(),
            (row.get("format_title") or "").strip(),
        ]
    )

    pairs, seen = [], set()
    for a in artists:
        for t in titles:
            if (a, t) not in seen:
                seen.add((a, t))
                pairs.append((a, t))
    return pairs


def _try_with_forms(lookup_fn, pairs):
    """各 (artist, title) ペアで lookup を試し、データが取れた時点で打ち切り。
    全試行の `_meta` を集約 (title_hit/artist_hit/both_hit は OR、
    other_artists は連結)。
    note: both_hit は同一試行内で同じ候補が両方マッチしたら True なので、
    複数試行を OR で重ねても「ある試行で両方そろった」を保つ。"""
    aggregated = {"title_hit": False, "artist_hit": False, "both_hit": False,
                  "other_artists": []}
    last_error = None
    last_partial = None
    for a, t in pairs:
        r = lookup_fn(a, t)
        m = r.get("_meta") or {}
        aggregated["title_hit"] = aggregated["title_hit"] or m.get("title_hit", False)
        aggregated["artist_hit"] = aggregated["artist_hit"] or m.get("artist_hit", False)
        aggregated["both_hit"] = aggregated["both_hit"] or m.get("both_hit", False)
        aggregated["other_artists"].extend(m.get("other_artists", []) or [])
        if "_error" in r:
            last_error = r["_error"]
        if r.get("_partial"):
            last_partial = r.get("_partial")
        # データが返ったら採用 (title_hit/artist_hit は履歴を残す)
        if any(k in r for k in FIELDS):
            r["_meta"] = aggregated
            return r
    final = {"_meta": aggregated}
    if last_error:
        final["_error"] = last_error
    if last_partial:
        final["_partial"] = last_partial
    return final


def process_row(row: dict, skip_apis=()) -> dict:
    """
    skip_apis: 呼ばない API 名（"musicbrainz" / "vocadb" / "itunes"）。アプリ版のみの引数。
    返り値:
      {"_skipped": True}                                       # 全フィールド既入力
      {"_error": "..."}                                        # 致命的エラー
      {
        "fields": {field: (value, source)},
        "note": "warning",
        "diagnostics": {api_name: {"candidates":N,"title_hit":bool,"artist_hit":bool}},
        "still_missing": [...]
      }
    スタック順: MusicBrainz → VocaDB → iTunes
    各 API 内で _get_search_pairs が返す (artist, title) を優先度順に試行。
    """
    needed = [k for k in FIELDS if is_blank(row.get(k))]
    if not needed:
        return {"_skipped": True}

    pairs = _get_search_pairs(row)
    if not pairs:
        return {"_error": "missing name/artist or title"}

    fields = {}
    notes = []
    diagnostics = {}

    # 1) MusicBrainz
    m = {} if "musicbrainz" in skip_apis else _try_with_forms(mb_lookup, pairs)
    diagnostics["musicbrainz"] = m.get("_meta", {})
    if "musicbrainz" in skip_apis:
        diagnostics.pop("musicbrainz")
    if m.get("_error"):
        notes.append(m["_error"])
    if m.get("_partial"):
        notes.append(m["_partial"])
    for f in needed[:]:
        if f in m:
            fields[f] = (m[f], "musicbrainz")
            needed.remove(f)

    # 2) VocaDB (残ったフィールドのみ)
    if needed and "vocadb" not in skip_apis:
        v = _try_with_forms(vocadb_lookup, pairs)
        diagnostics["vocadb"] = v.get("_meta", {})
        if v.get("_error"):
            notes.append(v["_error"])
        for f in needed[:]:
            if f in v:
                fields[f] = (v[f], "vocadb")
                needed.remove(f)

    # 3) iTunes (year + composer)
    # 何か残っていれば呼ぶ (lyricist しか残ってない場合は iTunes に lyricist が無いので
    # 呼んでも無駄だが、composer が空のまま MB+VocaDB が両方コケた場合や year が
    # 残っている場合に拾えるので、条件をフィールド限定にせず "needed が空でない" に統一)
    if needed and "itunes" not in skip_apis:
        s = _try_with_forms(itunes_lookup, pairs)
        diagnostics["itunes"] = s.get("_meta", {})
        if s.get("_error"):
            notes.append(s["_error"])
        for f in needed[:]:
            if f in s:
                fields[f] = (s[f], "itunes")
                needed.remove(f)

    return {
        "fields": fields,
        "note": " / ".join(notes),
        "diagnostics": diagnostics,
        "still_missing": needed,
    }


# ----------------------------------------------------------- アプリ用の入口
def build_row_for_song(song, artist_name=None, title=None):
    """
    Song から process_row() に渡す row を組み立てる。

    画面で歌手名・曲名を書き換えて未保存のまま取得することがあるので、
    artist_name / title が DB の値と違うときはそちらを使い、DB 由来の
    名義・別表記・正規化名は混ぜない（直す前の表記で当たってしまうのを避ける）。
    """
    from .utils import normalize

    artist = song.artist
    artist_name = (artist_name or "").strip() or artist.name
    title = (title or "").strip() or song.title

    row = {"name": artist_name, "title": title}

    if artist_name == artist.name:
        row["format_name"] = artist.format_name or ""
        credit = song.credit
        if credit and credit.name != artist.name:
            row["credit_name"] = credit.name
            row["credit_format_name"] = credit.format_name or ""
        aliases = list(artist.aliases.all())
        row["alias_names"] = NAME_SEP.join(a.name for a in aliases)
        row["alias_format_names"] = NAME_SEP.join(a.format_name for a in aliases if a.format_name)
    else:
        row["format_name"] = normalize(artist_name)

    row["format_title"] = (song.format_title or "") if title == song.title else normalize(title)
    return row


def fetch_credits_for_song(song, artist_name=None, title=None, current=None):
    """
    1曲ぶん取得する。current は画面上の現在値 {lyricist, composer, year}
    （空の項目だけを取りに行く）。省略時は DB の値を使う。

    返り値: process_row() の結果に "pairs"（試した表記の組み合わせ）を足したもの。
    """
    row = build_row_for_song(song, artist_name, title)
    if current is None:
        current = {"lyricist": song.lyricist, "composer": song.composer, "year": song.year}
    for f in FIELDS:
        row[f] = current.get(f) or ""

    from django.conf import settings

    skip = tuple(getattr(settings, "CREDIT_FETCH_SKIP_APIS", ()))
    result = process_row(row, skip_apis=skip)
    result["pairs"] = _get_search_pairs(row)
    result["skipped_apis"] = list(skip)
    return result
