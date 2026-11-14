"""
MK - automatic "Update available" banner.

Runs on GitHub every hour (.github/workflows/play-update.yml). Asks the Google Play Developer API which MK
releases are live (lifecycle state PUBLISHED) on the production and testing tracks. If the newest one is newer
than appUpdate.latest in data.json, it writes the new version into data.json (and bumps data.json's version so
phones download it). MK then shows "Update available" and sends one notification per version (MK 1.0.7+).

It never removes or lowers anything, keeps the "minimum version" (emergency) setting as it is, and changes
nothing else in data.json. Read-only on Google Play: the service account only needs "View app information".
"""
import datetime, json, os, re, sys

PACKAGE = os.environ.get("PLAY_PACKAGE", "com.mohankumar.mk")
TRACKS = [t.strip() for t in os.environ.get("PLAY_TRACKS", "production,beta,alpha").split(",") if t.strip()]
DATA = os.environ.get("DATA_FILE", "data.json")
API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{pkg}/tracks/{track}/releases"


def live_releases(get_json):
    """[(versionCode, versionName, track)] for every release that is live (PUBLISHED) on the tracks."""
    out = []
    for track in TRACKS:
        status, body = get_json(API.format(pkg=PACKAGE, track=track))
        if status in (204, 404):               # track empty / not used yet (for example production before launch)
            continue
        if not 200 <= status < 300:
            raise SystemExit(f"Google Play answered {status} for track '{track}': {str(body)[:300]}")
        for r in (body or {}).get("releases", []):
            if r.get("releaseLifecycleState") != "RELEASE_LIFECYCLE_STATE_PUBLISHED":
                continue
            codes = [int(a["versionCode"]) for a in r.get("activeArtifacts", []) if str(a.get("versionCode", "")).isdigit()]
            if not codes:
                continue
            name = r.get("releaseName", "")
            m = re.search(r"\(([^()]+)\)\s*$", name)          # Play's default release name: "6 (1.0.6)"
            out.append((max(codes), (m.group(1) if m else name).strip()[:12], track))
    return out


def apply(data, releases, today):
    """Returns (changed, message). Only ever raises appUpdate.latest."""
    if not releases:
        return False, "No live release found on " + ", ".join(TRACKS)
    code, name, track = max(releases)
    old = data.get("appUpdate") or {}
    if int(old.get("latest") or 0) >= code:
        return False, f"Already up to date: MK {old.get('name') or old.get('latest')} ({old.get('latest')}). Newest live: {code} on {track}"
    upd = {"latest": code, "name": name}
    if old.get("notes") and int(old.get("latest") or 0) == code:
        upd["notes"] = old["notes"]
    for k in ("min", "minMsg"):                 # the emergency setting is only ever changed by hand
        if k in old:
            upd[k] = old[k]
    data["appUpdate"] = upd
    v = int(today.replace("-", "") + "01")
    data["version"] = max(v, int(data.get("version") or 0) + 1)
    data["updated"] = today
    return True, f"New version live on {track}: MK {name} ({code}). Banner set."


def main():
    raw = os.environ.get("PLAY_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise SystemExit("Missing secret PLAY_SERVICE_ACCOUNT_JSON (GitHub → Settings → Secrets and variables → Actions).")
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/androidpublisher"])
    session = AuthorizedSession(creds)

    def get_json(url):
        r = session.get(url, timeout=30)
        if r.status_code == 204 or not r.content:
            return r.status_code, {}
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text

    releases = live_releases(get_json)
    with open(DATA, encoding="utf-8") as f:
        data = json.load(f)
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).date().isoformat()
    changed, msg = apply(data, releases, today)
    print(msg)
    for c, n, t in sorted(releases, reverse=True):
        print(f"  live: {t:<10} {c} ({n})")
    if changed:
        with open(DATA, "w", encoding="utf-8") as f:     # same layout as the admin page writes
            f.write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### MK Play update check\n{msg}\n")


if __name__ == "__main__":
    main()
