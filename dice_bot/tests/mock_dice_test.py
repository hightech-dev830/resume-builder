"""End-to-end test of the bot against a mock Dice site (no real Dice, no AI, no Supabase).

Playwright intercepts www.dice.com and serves pages shaped like the real ones: the recommended list,
job pages, and the Easy Apply wizard (a preloaded resume that has to be Replaced through the "…" menu,
cover letter upload, an extra-question step, review, Submit). The AI answer is canned; the real
extension-v2 engine still writes the Word files. Run:  .venv\\Scripts\\python tests\\mock_dice_test.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config, llm, store  # noqa: E402
from bot.browser import Browser  # noqa: E402
from bot.runner import Run  # noqa: E402

IDS = {
    "good": "11111111-aaaa-4bbb-8ccc-000000000001",      # remote, today, preloaded resume -> applied
    "hybrid": "11111111-aaaa-4bbb-8ccc-000000000002",    # remote card, but the JD wants 3 office days -> skipped on the job page
    "onsite": "11111111-aaaa-4bbb-8ccc-000000000003",    # Austin, TX -> skipped on the card
    "old": "11111111-aaaa-4bbb-8ccc-000000000004",       # posted 5 days ago -> skipped on the card
    "extra": "11111111-aaaa-4bbb-8ccc-000000000005",     # wizard asks an extra question the rules cannot answer -> needs you
    "fresh": "11111111-aaaa-4bbb-8ccc-000000000006",     # no preloaded resume (upload area) -> applied
    "nobullet": "11111111-aaaa-4bbb-8ccc-000000000007",  # like real Dice: location and date in separate elements, the dot drawn with CSS -> applied
    "hybridtitle": "11111111-aaaa-4bbb-8ccc-000000000008",  # "(Hybrid)" in the title -> skipped on the card
    "nodate": "11111111-aaaa-4bbb-8ccc-000000000009",    # no date on the card; job page "Posted 60+ days ago • Updated 10 hours ago" -> applied
}
CARDS = [("good", "Edgewater Federal Solutions, Inc.", "Full-Stack Platform Engineer", "Remote", "Today"),
         ("hybrid", "Motional", "Senior Full Stack Engineer, Data Mining", "Remote or Boston, Massachusetts", "Today"),
         ("onsite", "Acme", "Backend Engineer", "Austin, TX", "Today"),
         ("old", "Aledade Inc.", "Senior Software Engineer I- Fullstack", "Remote", "5 days ago"),
         ("extra", "Culmen International LLC", "Sr. Full-Stack Engineer", "Remote", "Today"),
         ("fresh", "Atlassian Inc.", "Senior Fullstack Software Engineer, DX", "Remote", "Yesterday"),
         ("nobullet", "UnitedHealth Group", "Senior Software Engineer - Remote", "Remote", "Today"),
         ("hybridtitle", "CrowdStrike, Inc.", "Sr. Software Engineer - Sensor, macOS (Hybrid)", "Remote", "Today"),
         ("nodate", "Toast", "Senior Software Engineer, Release Engineering", "Remote", "")]
JD = ("About the role\nWe build developer platforms.\nWhat we're looking for\n- 7+ years of backend development in Python or Go\n"
      "- React, PostgreSQL, Kafka, AWS, Kubernetes\n- Elasticsearch search\n" + "We are a fully remote team across the US. " * 12)


def meta(k, loc, when):
    if k in ("nobullet", "hybridtitle", "nodate"):  # real Dice: separate elements, the dot is CSS (not in the text)
        return f'<div class="meta"><span>{loc}</span><span class="dot"></span>{f"<span>{when}</span>" if when else ""}</div>'
    return f"<div>{loc} • {when}</div>"


def list_page(page_no: int) -> str:
    if page_no > 1:
        return "<html><body><h2>No more recommendations</h2></body></html>"
    cards = "".join(f"""<div class="card"><div class="top"><a href="/company/x">{co}</a><button>save</button>
      <a class="btn" href="/job-detail/{IDS[k]}">Apply Now</a></div><a href="/job-detail/{IDS[k]}"><h5>{t}</h5></a>
      {meta(k, loc, when)}<div><span>Full-time</span><span>USD 150,000.00 - 190,000.00 per year</span></div></div>""" for k, co, t, loc, when in CARDS)
    return f"<html><body><h2>164 jobs recommended for you</h2>{cards}</body></html>"


def head_meta(key, loc):
    tag = "On-site" if key == "onsite" else "Remote"
    if key == "nodate":
        return f"<p>{loc} in San Francisco, CA, US • Posted 60+ days ago • Updated 10 hours ago</p><div><span>Full Time</span><span>{tag}</span></div>"
    if key == "nobullet":
        return f'<p><span>{loc}</span><span class="dot"></span><span>Posted today</span></p><div><span>Full Time</span><span>{tag}</span></div>'
    return f"<p>{loc} • Posted today • Updated 1 hour ago</p><div><span>Full Time</span><span>{tag}</span></div>"


def apply_js(key):
    url = f"/job-applications/{IDS[key]}/wizard"
    if key == "good":
        return f"window.open('{url}', '_blank')"   # real Dice: the wizard opens in a new tab
    if key == "nobullet":
        return "void 0"                              # a click that does nothing visible: the bot must go to the address itself
    return f"location.href='{url}'"


def job_page(key: str) -> str:
    _, co, t, loc, when = next(c for c in CARDS if c[0] == key)
    jd = JD + ("\nThis role is hybrid: 3 days per week in our Boston office." if key == "hybrid" else "")
    return f"""<html><body><main><div class="hdr"><a href="/company-profile/x">{co}</a><button>save</button>
    <button onclick="{apply_js(key)}">Apply</button>
    <h1>{t}</h1>{head_meta(key, loc)}</div>
    <section><h2>Job Details</h2><p>{jd.replace(chr(10), '<br>')}</p></section><aside><h2>Similar Jobs</h2></aside></main></body></html>"""


def wizard_page(key: str) -> str:
    _, co, t, *_ = next(c for c in CARDS if c[0] == key)
    return """<html><body><p>You're Applying for</p><h3>%s</h3><p>@ %s in Remote</p><div id="app"></div>
<input type="file" id="rf" hidden style="display:none"><input type="file" id="cf" hidden>
<script>
const KEY = %s; let resume = KEY === 'good' || KEY === 'extra' ? 'Zachary_Mosley_AI_Software_Engineer_Resume.docx' : '';
let cover = '', step = 1, answer = '';
const app = document.getElementById('app');
rf.onchange = () => { resume = rf.files[0].name; render(); };
cf.onchange = () => { cover = cf.files[0].name; render(); };
function render() {
  if (step === 1) app.innerHTML = `<p>Step 1 of 2</p><h2>Resume &amp; Cover Letter</h2>
    <div class="box"><div>Resume *</div>${resume ? `<div><span>${resume}</span><div id="menu" class="icon-wrap" style="display:inline-block;width:24px;height:24px;cursor:pointer"><svg width="20" height="6" viewBox="0 0 20 6"><circle cx="3" cy="3" r="2"/><circle cx="10" cy="3" r="2"/><circle cx="17" cy="3" r="2"/></svg></div>
      <div id="mn" hidden><div id="rep">Replace</div><div>Delete</div></div></div><div>Uploaded to profile on 11/17/2026</div>`
      : `<div class="drop" id="rdrop">Upload your resume</div>`}</div>
    <div class="box"><div><span>Cover letter</span><i>Optional</i></div>${cover ? `<span>${cover}</span>` : '<div class="drop" id="cdrop">Upload your cover letter</div>'}
      <div>File types supported .pdf, .doc, .docx, .txt, .rtf up to 2MB</div></div><button id="next">Next</button>`;
  else if (step === 2) app.innerHTML = `<p>Step 2 of 3</p><h2>Additional questions</h2>
    <label for="q1">What is your favorite internal project codename? *</label><input id="q1" required><button id="next">Next</button>`;
  else if (step === 3) app.innerHTML = `<p>Step 2 of 2</p><h2>Review your application</h2><div>Resume *</div><div>${resume}</div>
    <div>Cover Letter</div><div>${cover || 'No cover letter uploaded'}</div><div>Work Authorization *</div><div>US Citizen</div><button>Back</button><button id="submit">Submit</button>`;
  else app.innerHTML = '<h2>Application submitted!</h2><p>Your application was sent to the employer.</p>';
  const $ = (id) => document.getElementById(id);
  if ($('menu')) $('menu').onclick = () => { $('mn').hidden = false; };
  if ($('rep')) $('rep').onclick = () => { const i = document.createElement('input'); i.type = 'file'; i.hidden = true;
    i.onchange = () => { resume = i.files[0].name; render(); }; document.body.appendChild(i); i.click(); };
  if ($('rdrop')) $('rdrop').onclick = () => rf.click();
  if ($('cdrop')) $('cdrop').onclick = () => cf.click();
  if ($('next')) $('next').onclick = () => { if (step === 1 && !resume) return; step = step === 1 && KEY === 'extra' ? 2 : 3; render(); };
  if ($('submit')) $('submit').onclick = () => { step = 4; render(); };
}
render();
</script></body></html>""" % (t, co, json.dumps(key))


def route(r):
    url = r.request.url
    key = next((k for k, v in IDS.items() if v in url), None)
    if "/recommended-jobs" in url:
        body = list_page(int(url.split("page=")[-1]) if "page=" in url else 1)
    elif "/job-applications/" in url and key:
        body = wizard_page(key)
    elif "/job-detail/" in url and key:
        body = job_page(key)
    else:
        return r.fulfill(status=404, body="not found")
    r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)


class MockBrowser(Browser):
    """Headless, throwaway profile, every www.dice.com request answered by the mock site."""

    def __init__(self, tmp):
        super().__init__("chrome")
        self.tmp = tmp

    def start(self):
        if self.ctx:
            return self
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(str(Path(self.tmp) / "profile"), channel="chrome", headless=True)
        self.ctx.route("https://www.dice.com/**", route)
        return self


ANSWER = {
    "company_industry": "Developer platforms", "label": "Senior Full-Stack Engineer",
    "summary": "Senior engineer with 12 years building Python and Go services and React front ends on PostgreSQL, Kafka and Elasticsearch, running on AWS and Kubernetes.",
    "highlights": [], "skills": [{"name": "Backend", "keywords": ["Python", "Go", "PostgreSQL", "Apache Kafka", "Elasticsearch"]},
                                 {"name": "Frontend", "keywords": ["React"]}, {"name": "Cloud", "keywords": ["AWS", "Kubernetes"]}],
    "work": [{"index": 0, "position": "Senior Software Engineer", "description": "Clinic scheduling platform.",
              "highlights": ["Built appointment search on Elasticsearch over PostgreSQL.", "Moved HL7 imports to Kafka consumers in Go.",
                             "Ran 30 services on Kubernetes in AWS.", "Added React views for clinic staff."]}],
    "keywords_matched": [],
}


class FakeSupabase:
    rows: list = []
    ready = True

    def __init__(self, *a):
        pass

    def insert_bid(self, row):
        FakeSupabase.rows.append(row)
        return {"id": len(FakeSupabase.rows)}

    def already_applied(self, url):
        return False

    def applied_today(self):
        return 0


class UI:
    def __init__(self):
        self.lines = []

    def log(self, t, level="info"):
        self.lines.append((level, t))
        print(f"  [{level}] {t}")

    def status(self, t):
        pass

    def job(self, r):
        pass

    def count(self, n, limit):
        pass


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="dicebot_")
    # keep the real local log untouched
    store.LEDGER_FILE = Path(tmp) / "bids.jsonl"
    import bot.runner as runner_mod
    runner_mod.ledger_add = store.ledger_add = lambda e, f=store.LEDGER_FILE: f.open("a").write(json.dumps({"t": 0, **e}) + "\n")
    runner_mod.ledger_seen = lambda job_id: ""
    runner_mod.ledger_applied_today = lambda: 0
    runner_mod.Supabase = FakeSupabase
    llm.Chat.send = lambda self, text, json_mode=True: ("```json\n" + json.dumps(ANSWER) + "\n```") if json_mode else \
        "Dear team,\n\nI am applying for this role.\n\nBest regards,\nJordan Avery"

    settings = {**config.DEFAULTS, "profile_path": str(config.REPO / "extension-v2/profiles/sample-profile.json"), "output_dir": str(Path(tmp) / "bids"),
                "engine": "openai", "max_age_days": 2, "delay_min": 0, "delay_max": 0, "max_pages": 3, "fix_rounds": 0,
                "supabase_url": "x", "supabase_key": "x"}
    ui = UI()
    browser = MockBrowser(tmp)
    try:
        Run(settings, browser, ui, threading.Event()).run()
        # leave the browser open until the checks are done (the "needs you" tab must still be there)
        open_tabs = [p.url for p in browser.ctx.pages]
    finally:
        browser.close()

    results = {}
    for line in store.LEDGER_FILE.read_text().splitlines():
        e = json.loads(line)
        results[next(k for k, v in IDS.items() if v == e["job_id"])] = e
    fails = 0

    def check(ok, msg):
        nonlocal fails
        print(("  ✔ " if ok else "  ✖ ") + msg)
        fails += 0 if ok else 1

    print("\nChecks")
    check(results.get("good", {}).get("status") == "applied", f"wizard in a NEW TAB, SVG '…' menu -> Replace, submitted: {results.get('good', {}).get('why')}")
    reps = [t for _, t in ui.lines if "replaced Zachary" in t]
    check(bool(reps) and all("menu" in t for t in reps), f"preloaded resume swapped through the '…' menu: {reps[:1]}")
    check(results.get("fresh", {}).get("status") == "applied", f"empty resume slot uploaded and submitted: {results.get('fresh', {}).get('why')}")
    check(results.get("hybrid", {}).get("status") == "skipped" and "not remote" in results["hybrid"]["why"], f"hybrid JD skipped on the job page: {results.get('hybrid', {}).get('why')}")
    check(results.get("onsite", {}).get("status") == "skipped" and "on-site" in results["onsite"]["why"], f"Austin (tagged On-site) skipped on the job page: {results.get('onsite', {}).get('why')}")
    check("old" not in results and "hybridtitle" not in results, "5-days-old card and '(Hybrid)' title skipped on the list")
    check(results.get("nobullet", {}).get("status") == "applied", f"card without '•' + Apply button that does nothing -> wizard opened by address: {results.get('nobullet', {}).get('why')}")
    check(results.get("nodate", {}).get("status") == "applied", f"card without a date, job page 'Updated 10 hours ago': {results.get('nodate', {}).get('why')}")
    check(not any("no location" in t for _, t in ui.lines), "no 'no location' skips")
    skips = [t for lvl, t in ui.lines if "skip " in t]
    check(skips and all("save" not in t and "Apply Now" not in t for t in skips), f"company names on cards read cleanly: {skips[:1]}")
    check(results.get("extra", {}).get("status") == "attention", f"unanswerable extra question left for you: {results.get('extra', {}).get('why')}")
    check(any(IDS["extra"] in u for u in open_tabs), "the 'needs you' wizard tab is still open")
    check(len(FakeSupabase.rows) == 4, f"only successful bids saved to Supabase ({len(FakeSupabase.rows)})")
    if FakeSupabase.rows:
        r = FakeSupabase.rows[0]
        folder = Path(r["resume_dir"])
        names = sorted(p.name for p in folder.iterdir())
        print(f"    folder {folder.relative_to(Path(tmp))}: {names}")
        check(folder.name == "Edgewater_Federal_Solutions_Inc" and "Jordan_Avery_cv.docx" in names and "Jordan_Avery_cover_letter.docx" in names,
              "files in <date>/<Company>/ named <First>_<Last>_cv.docx and _cover_letter.docx")
        check(r["source"] == "dice" and r["status"] == "applied" and r["job_link"].endswith(IDS["good"]) and r["resume_json"] and r["cover_letter"] and r["jd"],
              "Supabase row has source, status, link, resume JSON, cover letter and JD")
    print("\n" + ("all checks passed" if not fails else f"{fails} check(s) failed"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
