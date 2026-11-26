import os, re
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # without this the browser tool cannot call the server

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
JUNK_DOM = ("linkedin.com", "licdn.com", "linkedin.co", "example.com", "sentry.io", "domain.com")
JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")


def clean(found):
    out = set()
    for e in found:
        e = e.strip().strip(".,;:").lower()
        if not e or len(e) > 80:
            continue
        if e.endswith(JUNK_EXT):
            continue
        if any(e == d or e.endswith("@" + d) or e.endswith("." + d) for d in JUNK_DOM):
            continue
        out.add(e)
    return sorted(out)


@app.route("/health")
def health():
    return jsonify(ok=True)


@app.route("/scrape")
def scrape():
    url = (request.args.get("url") or "").strip()
    if "linkedin.com/in/" not in url:
        return jsonify(error="not a linkedin profile url"), 400
    url = url.split("?")[0].rstrip("/")

    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    li_at = (os.environ.get("LI_AT") or "").strip()
    if li_at:
        headers["Cookie"] = "li_at=" + li_at

    try:
        r = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        html = r.text
        blocked = "/authwall" in r.url or "authwall" in html[:3000]

        if blocked:
            r = requests.get(url, headers={**headers, "User-Agent": BOT_UA}, timeout=20)
            html = r.text
            blocked = "/authwall" in r.url or "authwall" in html[:3000]

        emails = clean(EMAIL_RE.findall(html))
        note = "" if emails else ("page is behind LinkedIn's authwall" if blocked else "no email on this page")
        return jsonify(emails=emails, status=r.status_code, blocked=blocked, note=note)
    except Exception as e:
        return jsonify(error=type(e).__name__ + ": " + str(e)), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
