# scraper.py — Render: gunicorn scraper:app --workers 1 --threads 8
# Env vars: LI_AT (LinkedIn cookie), HUNTER_KEY, ANIMAIL_KEY
import os, re, time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

LI_AT = os.environ.get("LI_AT", "").strip()
HUNTER_KEY = os.environ.get("HUNTER_KEY", "").strip()

UA_NAV = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
UA_BOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I)
NAME_RE = re.compile(r"\b([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+){1,2})\b")
BAD_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|ico|css|js|json|xml)$", re.I)
JUNK = ("sentry.io", "wixpress.com", "example.com", "domain.com", "cloudflare.com",
        "schema.org", "w3.org", "gstatic.com", "googleapis.com", "sentry-next")

PATHS = ["", "/about", "/about-us", "/our-team", "/team", "/people", "/staff",
         "/contact", "/contact-us", "/leadership", "/who-we-are", "/company", "/our-people"]

def get(url, bot=False, cookies=None):
    h = {"User-Agent": UA_BOT if bot else UA_NAV, "Accept-Language": "en-US,en;q=0.9"}
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return requests.get(url, headers=h, timeout=15, allow_redirects=True)

def extract(html, src):
    pairs = []
    for m in EMAIL_RE.finditer(html):
        ctx = html[max(0, m.start() - 160):m.start()]
        n = NAME_RE.search(ctx)
        pairs.append((m.group(0), n.group(1) if n else "", src))
    return pairs

def clean(pairs, domain=None):
    seen, out = set(), []
    for e, name, src in pairs:
        e = (e or "").lower().strip(".,;:()<>\"'")
        if not e or len(e) > 70 or BAD_EXT.search(e) or ".." in e:
            continue
        if any(j in e for j in JUNK):
            continue
        if domain and not e.endswith("@" + domain):
            continue
        if e in seen:
            continue
        seen.add(e)
        out.append({"email": e, "name": name, "source": src})
    return out

@app.route("/")
def root():
    return jsonify(service="email-scraper", ok=True,
                   li_at_configured=bool(LI_AT), hunter_configured=bool(HUNTER_KEY))

@app.route("/health")
def health():
    return jsonify(ok=True, li_at_configured=bool(LI_AT), hunter_configured=bool(HUNTER_KEY))

@app.route("/scrape")
def scrape():
    url = request.args.get("url", "")
    if "linkedin.com/in/" not in url:
        return jsonify(error="not a linkedin profile url"), 400
    cookies = {"li_at": LI_AT} if LI_AT else None
    try:
        r = get(url, cookies=cookies)
        pairs = extract(r.text, "profile")
        blocked = r.status_code in (999, 429) or (not pairs and "authwall" in r.text.lower())
        if blocked and LI_AT:
            r = get(url, bot=True)
            pairs += extract(r.text, "profile-bot")
            blocked = r.status_code in (999, 429)
        m = re.search(r"/in/([A-Za-z0-9_\-%]+)", url)
        if m and LI_AT:
            try:
                ci = requests.get(
                    "https://www.linkedin.com/voyager/api/identity/profiles/" + m.group(1) + "/profileContactInfo",
                    headers={"Accept": "application/vnd.linkedin.normalized+json+2.1",
                             "csrf-token": "ajax:0", "x-restli-protocol-version": "2.0.0",
                             "User-Agent": UA_NAV},
                    cookies=cookies, timeout=12)
                if ci.ok:
                    pairs += [(e, "", "contact-info") for e in EMAIL_RE.findall(ci.text)]
            except Exception:
                pass
        emails = clean(pairs)
        if blocked and not emails:
            return jsonify(blocked=True, emails=[], status=r.status_code,
                           note="authwall — refresh LI_AT or use domain scan")
        return jsonify(emails=[e["email"] for e in emails], count=len(emails), status=r.status_code)
    except Exception as ex:
        return jsonify(error=str(ex)), 500

def hunter_search(domain):
    if not HUNTER_KEY:
        return [], ""
    try:
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": HUNTER_KEY, "limit": 100}, timeout=25)
        if not r.ok:
            return [], ""
        data = r.json().get("data", {})
        out = []
        for e in data.get("emails", []):
            out.append({
                "email": e.get("value", ""),
                "name": ((e.get("first_name") or "") + " " + (e.get("last_name") or "")).strip(),
                "position": e.get("position") or "",
                "confidence": e.get("confidence", 0),
                "source": "hunter",
            })
        return out, data.get("pattern") or ""
    except Exception:
        return [], ""

@app.route("/scan-domain")
def scan_domain():
    domain = request.args.get("domain", "").strip().lower()
    domain = re.sub(r"^https?://", "", domain).strip("/").split("/")[0]
    if "." not in domain:
        return jsonify(error="bad domain"), 400
    use_hunter = request.args.get("hunter") == "1"

    base = "https://" + domain
    found, scanned, tries = [], 0, 0
    for p in PATHS:
        if tries >= 10:
            break
        tries += 1
        try:
            r = get(base + p)
            if r.ok:
                found += extract(r.text, base + p)
                scanned += 1
        except Exception:
            continue
        time.sleep(0.2)

    try:
        sm = get(base + "/sitemap.xml")
        if sm.ok:
            for loc in re.findall(r"<loc>([^<]+)</loc>", sm.text)[:400]:
                if tries >= 16:
                    break
                if not re.search(r"team|staff|people|about|contact|leadership", loc, re.I):
                    continue
                tries += 1
                try:
                    r = get(loc)
                    if r.ok:
                        found += extract(r.text, loc)
                        scanned += 1
                except Exception:
                    pass
    except Exception:
        pass

    site_emails = clean(found, domain)
    hunter_emails, pattern = ([], "")
    if use_hunter:
        hunter_emails, pattern = hunter_search(domain)

    return jsonify(domain=domain, site_emails=site_emails, hunter_emails=hunter_emails,
                   pattern=pattern, pages_scanned=scanned)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
