"""Wavelength backend: serves the app and wraps ytmusicapi for search, charts and radio.
Audio/video is NOT proxied. The app plays tracks through YouTube's official embedded player."""
import os
import time
from threading import Lock

from flask import Flask, jsonify, request, send_from_directory

from ytmusicapi import YTMusic

BASE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = {"index.html", "manifest.json", "sw.js", "icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png"}
app = Flask(__name__, static_folder=None)
DEFAULT_COUNTRY = os.environ.get("WAVELENGTH_COUNTRY", "IN")

_yt = {}
_lock = Lock()
_cache = {}
TTL = 600


def client(country):
    with _lock:
        if country not in _yt:
            _yt[country] = YTMusic(language="en", location=country if country != "ZZ" else "")
        return _yt[country]


def cached(key, fn, ttl=TTL):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    if len(_cache) > 300:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
            _cache.pop(k, None)
    return val


def pick_thumb(thumbs):
    if not thumbs:
        return None
    ordered = sorted(thumbs, key=lambda t: t.get("width", 0))
    for t in ordered:
        if t.get("width", 0) >= 200:
            return t.get("url")
    return ordered[-1].get("url")


def track(t):
    vid = t.get("videoId")
    if not vid:
        return None
    artists = t.get("artists")
    if artists:
        artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
    else:
        artist = t.get("artist") or ""
    album = t.get("album")
    album = album.get("name") if isinstance(album, dict) else album
    thumbs = t.get("thumbnails") or t.get("thumbnail") or []
    return {
        "id": vid,
        "title": t.get("title") or "Unknown",
        "artist": artist,
        "album": album or "",
        "thumb": pick_thumb(thumbs) or "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid,
        "duration": t.get("duration") or t.get("length") or "",
    }


def tracks(items):
    out, seen = [], set()
    for it in items or []:
        t = track(it)
        if t and t["id"] not in seen:
            seen.add(t["id"])
            out.append(t)
    return out


def country_arg():
    c = (request.args.get("country") or DEFAULT_COUNTRY).upper()
    return c if len(c) == 2 else DEFAULT_COUNTRY


def fail(e):
    return jsonify({"error": "Music service unavailable right now.", "detail": str(e)[:200]}), 502


@app.get("/api/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"tracks": []})
    try:
        res = cached(("s", q.lower(), country_arg()), lambda: client(country_arg()).search(q, filter="songs", limit=25))
        return jsonify({"tracks": tracks(res)})
    except Exception as e:  # network / parsing errors from the unofficial API
        return fail(e)


@app.get("/api/charts")
def charts():
    c = country_arg()

    def build():
        yt = client(c)
        data = yt.get_charts(c)
        vids = data.get("videos") or []
        if not vids:
            return {"title": "", "tracks": []}
        pl = yt.get_playlist(vids[0]["playlistId"], limit=40)
        return {"title": vids[0].get("title", "Top songs"), "tracks": tracks(pl.get("tracks"))}

    try:
        return jsonify(cached(("c", c), build))
    except Exception as e:
        return fail(e)


@app.get("/api/home")
def home():
    c = country_arg()

    def build():
        rows = client(c).get_home(limit=4)
        out = []
        for r in rows:
            ts = tracks(r.get("contents"))
            if len(ts) >= 3:
                out.append({"title": r.get("title", ""), "tracks": ts})
        return {"sections": out}

    try:
        return jsonify(cached(("h", c), build))
    except Exception as e:
        return fail(e)


@app.get("/api/next")
def nxt():
    vid = (request.args.get("v") or "").strip()
    if len(vid) != 11:
        return jsonify({"tracks": []})
    try:
        res = cached(("n", vid), lambda: client(country_arg()).get_watch_playlist(videoId=vid, radio=True, limit=20), ttl=120)
        return jsonify({"tracks": [t for t in tracks(res.get("tracks")) if t["id"] != vid]})
    except Exception as e:
        return fail(e)


@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/<name>")
def public_file(name):
    if name not in PUBLIC:
        return jsonify({"error": "Not found"}), 404
    return send_from_directory(BASE, name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
