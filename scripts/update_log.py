"""Reads Hana's conversations from Tavus and writes sessions.json for the page.
Only the math recap is kept: when, session, length, summary, stuck, next step. No names, video or perception data.
Run by GitHub Actions: right after a call (waits for the recap) and every 5 minutes as a backup."""
import json, os, re, subprocess, time, urllib.request
from datetime import datetime, timezone

PAL_ID = "p4f80ddd9679"
API = "https://tavusapi.com/v2"
KEY = os.environ["TAVUS_API_KEY"]
OUT = os.path.join(os.path.dirname(__file__), "..", "sessions.json")
AFTER_CALL = os.environ.get("WAIT_FOR_NEW") == "1"

def get(path):
    req = urllib.request.Request(API + path, headers={"x-api-key": KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def ts(s):
    if not s: return None
    s = str(s).replace("+00:00Z", "Z").replace(" ", "T")
    s = re.sub(r"(\.\d{6})\d*", r"\1", s).replace("Z", "+00:00")
    if not re.search(r"[+-]\d\d:\d\d$", s): s += "+00:00"
    try: return datetime.fromisoformat(s)
    except ValueError: return None

def age_min(when):
    t = ts(when)
    return (datetime.now(timezone.utc) - t).total_seconds() / 60 if t else 1e9

def line(text, label):
    m = re.search(r"^" + label + r":\s*(.+)$", text or "", re.M)
    return m.group(1).strip() if m else ""

def find_recap(x):
    """The recap is the post-call request whose text has a 'Summary:' line. Other post-call actions are skipped."""
    if isinstance(x, str):
        if re.search(r"^Summary:", x, re.M): return x
        try: return find_recap(json.loads(x))
        except ValueError: return ""
    if isinstance(x, dict):
        for k in ["body"] + [k for k in x if k != "body"]:
            if k in x:
                r = find_recap(x[k])
                if r: return r
    if isinstance(x, list):
        for v in x:
            r = find_recap(v)
            if r: return r
    return ""

def session_name(recap_session, ctx):
    ctx = ctx or ""
    for k, v in [("Lesson 1", "Lesson 1 · Equivalent fractions"), ("Lesson 2", "Lesson 2 · Adding fractions"),
                 ("Lesson 3", "Lesson 3 · Subtracting fractions"), ("Lesson 4", "Lesson 4 · Word problems")]:
        if k in ctx: return v
    if "My week" in ctx or re.search("my week", recap_session or "", re.I): return "My week"
    if re.search("adding", ctx, re.I): return "Lesson 2 · Adding fractions"
    return recap_session or "Session"

def load():
    try:
        with open(OUT) as f: return json.load(f)
    except (FileNotFoundError, ValueError): return []

def save(rows):
    rows.sort(key=lambda r: r["when"] or "", reverse=True)
    with open(OUT, "w") as f: json.dump(rows, f, indent=1, ensure_ascii=False)

def push():
    """Commit and push right away, so the page sees the change while the run keeps waiting."""
    if not os.environ.get("GITHUB_ACTIONS"): return
    run = lambda *a: subprocess.run(list(a), check=False)
    run("git", "config", "user.name", "session-log-bot")
    run("git", "config", "user.email", "actions@users.noreply.github.com")
    run("git", "add", "sessions.json")
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0: return
    run("git", "commit", "-q", "-m", "Update session log")
    run("git", "pull", "-q", "--rebase", "origin", "main")
    run("git", "push", "-q", "origin", "HEAD:main")
    print("pushed")

def update(rows):
    """One pass. Returns (changed, pending_left)."""
    done = {r["id"] for r in rows if r.get("summary") or (not r.get("pending") and age_min(r.get("when")) > 24 * 60)}
    rows[:] = [r for r in rows if r["id"] in done]  # rows still waiting for a recap are fetched again
    before = {r["id"] for r in rows}
    changed, pending = False, 0
    for c in get("/conversations?limit=50").get("data", []):
        cid = c["conversation_id"]
        if (c.get("pal_id") or c.get("persona_id")) != PAL_ID or c.get("status") != "ended" or cid in before:
            continue
        d = get(f"/conversations/{cid}?verbose=true")
        evs = d.get("events") or []
        ev = lambda t: next((e for e in evs if e.get("event_type") == t), None)
        plain = ""
        for e in evs:
            if e.get("event_type") == "application.post_call_action_executed":
                plain = find_recap((e.get("properties") or {}).get("request", {}))
                if plain: break
        start = ts((ev("system.replica_joined") or {}).get("timestamp")) or ts(d.get("created_at"))
        ended = ts((ev("system.shutdown") or {}).get("timestamp")) or ts(d.get("updated_at"))
        waiting = not plain and ended and (datetime.now(timezone.utc) - ended).total_seconds() < 20 * 60
        rows.append({"id": cid, "when": d.get("created_at"),
                     "session": session_name(line(plain, "Session"), d.get("conversational_context")),
                     "length_sec": round((ended - start).total_seconds()) if start and ended else None,
                     "summary": line(plain, "Summary"), "stuck": line(plain, "Got stuck on"),
                     "next": line(plain, "Next session"), "pending": bool(waiting)})
        pending += bool(waiting)
        changed = True
    return changed, pending

rows = load()
complete_at_start = {r["id"] for r in rows if r.get("summary")}
old = json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True)
deadline = time.time() + (7 * 60 if AFTER_CALL else 0)
while True:
    _, pending = update(rows)
    new = json.dumps(sorted(rows, key=lambda r: r["id"]), sort_keys=True)
    if new != old:
        save(rows); push(); old = new
    new_recap = any(r.get("summary") and r["id"] not in complete_at_start for r in rows)
    if time.time() > deadline or (new_recap and not pending):
        break
    time.sleep(15)
save(rows)
print(len(rows), "sessions,", sum(1 for r in rows if r.get("pending")), "waiting for a recap")
