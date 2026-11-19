"""One bidding run over Dice's recommended jobs.

For each job card: remote + date range (card) -> not applied before (Dice, local log, Supabase)
-> job page: read title / company / description, confirm remote again, Easy Apply available
-> tailored resume (+ cover letter) with the extension-v2 engine, with correction rounds
-> Word files in <output>/<date>/<Company>/<First>_<Last>_cv.docx (+ _cover_letter.docx)
-> Apply -> wizard: replace the resume, upload the cover letter, Next -> review -> Submit
-> success: saved to Supabase (resumes, source 'dice') and the local log.
Extra required questions the rules cannot answer: the tab is left open for you ("needs attention")."""
from __future__ import annotations

import json
import random
import re
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import dice, engine
from .config import DEBUG_DIR
from .llm import Chat, LLMError
from .store import Supabase, SupabaseError, bid_row, ledger_add, ledger_applied_today, ledger_seen


def slug(s: str, n: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", str(s or ""), flags=re.UNICODE).strip()
    return re.sub(r"[\s-]+", "_", s)[:n] or "Untitled"


def person_slug(profile: dict) -> str:
    name = (profile.get("resume", {}).get("basics", {}).get("name") or profile.get("personal", {}).get("full_name") or profile.get("name") or "Resume")
    return slug(name, 40)


class Run:
    def __init__(self, settings: dict, browser, ui, stop):
        self.s = settings
        self.browser = browser
        self.ui = ui              # .log(text, level) .status(text) .job(dict) .count(n, limit)
        self.stop = stop          # threading.Event
        self.sb = Supabase(settings["supabase_url"], settings["supabase_key"])
        self.applied_run = 0
        self.today = 0
        self.work = None          # the tab used for job pages / the wizard

    def log(self, msg, level="info"):
        self.ui.log(msg, level)

    # ---------- limits ----------
    def _today(self) -> int:
        n = self.sb.applied_today() if self.sb.ready else None
        return max(n or 0, ledger_applied_today())

    def _limit_reached(self) -> str:
        if self.today >= int(self.s["daily_limit"]):
            return f"daily limit reached ({self.today}/{self.s['daily_limit']})"
        if self.applied_run >= int(self.s["run_limit"]):
            return f"limit for this run reached ({self.applied_run})"
        return ""

    def _pause(self):
        lo, hi = int(self.s["delay_min"]), max(int(self.s["delay_min"]), int(self.s["delay_max"]))
        secs = random.uniform(lo, hi)
        self.ui.status(f"Waiting {int(secs)} s before the next job (account safety)…")
        end = time.time() + secs
        while time.time() < end and not self.stop.is_set():
            time.sleep(0.5)

    # ---------- main loop ----------
    def run(self):
        s = self.s
        if not s.get("profile_path"):
            raise RuntimeError("Choose your resume / profile JSON in Settings first.")
        self.profile = engine.load_profile(s["profile_path"])
        self.schema = engine.call("schema")
        self.log(f"Profile: {self.profile['name']} ({self.profile['totalYears']} years). Engine: {s['engine']}. "
                 f"Jobs posted within {s['max_age_days']} day(s), remote only.{' DRY RUN: Submit is not pressed.' if s['dry_run'] else ''}")
        if not self.sb.ready:
            self.log("Supabase is not set: successful bids are kept in the local log only.", "warn")
        self.today = self._today()
        self.ui.count(self.today, s["daily_limit"])
        if (why := self._limit_reached()):
            self.log(f"Not starting: {why}.", "warn")
            return
        self.browser.start()
        lst = self.browser.page()
        seen: set[str] = set()
        for page_no in range(1, int(s["max_pages"]) + 1):
            if self.stop.is_set():
                break
            self.ui.status(f"Reading recommended jobs, page {page_no}…")
            cards = dice.read_list(lst, page_no)
            if page_no == 1:
                # keep the real list page, to tune the reader if Dice changes its layout
                try:
                    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
                    (DEBUG_DIR / "list_page1.html").write_text(lst.content(), encoding="utf-8")
                except Exception:
                    pass
                if cards and not any(c.get("posted") or c.get("location") for c in cards):
                    self.log("The list cards show no location or date the bot can read; each job page is checked instead. "
                             f"(Send {DEBUG_DIR / 'list_page1.html'} to update the list reader.)", "warn")
            if not cards:
                self.log(f"Page {page_no}: no more jobs.")
                break
            fresh = [c for c in cards if c["id"] not in seen]
            seen.update(c["id"] for c in cards)
            self.log(f"Page {page_no}: {len(cards)} jobs")
            if not fresh:
                break
            for card in fresh:
                if self.stop.is_set():
                    break
                if (why := self._limit_reached()):
                    self.log(f"Stopping: {why}.", "warn")
                    return
                if self._skip_card(card):
                    continue
                result = self._job(card)
                if result == "applied":
                    self.applied_run += 1
                    self.today += 1
                    self.ui.count(self.today, s["daily_limit"])
                if result in ("applied", "attention", "failed") and not self.stop.is_set() and not self._limit_reached():
                    self._pause()
        self.ui.status("Finished")
        self.log(f"Run finished: {self.applied_run} applied. Today: {self.today}/{s['daily_limit']}.", "ok")

    def _skip_card(self, card) -> bool:
        def skip(why, quiet=False):
            if not quiet:
                self.log(f"  skip {card['title'][:60]} @ {card['company'][:30]}: {why}", "dim")
            return True
        if card.get("applied"):
            return skip("already applied on Dice", quiet=True)
        if (prev := ledger_seen(card["id"])) in ("applied", "attention", "skipped"):
            return skip(f"done before ({prev})", quiet=True)
        # the list only weeds out what is clearly not remote; the job page makes the real remote check
        if card.get("hybridWord"):
            return skip("hybrid (on the card)")
        if card.get("onsiteWord") and not card.get("remoteWord"):
            return skip("on-site (on the card)")
        # date range: from the card when it shows one, else checked on the job page
        age = dice.age_days(card.get("posted", ""))
        if age is not None and age > int(self.s["max_age_days"]) - 1:
            return skip(f"posted '{card['posted']}' (outside {self.s['max_age_days']} day(s))")
        if self.sb.ready and self.sb.already_applied(card["url"]):
            return skip("already in Supabase")
        return False

    # ---------- one job ----------
    def _job(self, card) -> str:
        steps: list[str] = []

        def say(msg, level="info"):
            steps.append(msg)
            self.log(msg, level)

        title = f"{card['title']} @ {card['company']}"
        self.ui.status(f"Job: {title}")
        row = {"time": datetime.now().strftime("%H:%M"), "company": card["company"], "title": card["title"], "status": "reading", "ats": "", "risk": "", "url": card["url"]}
        self.ui.job(row)
        try:
            if self.work is None or self.work.is_closed():
                self.work = self.browser.page()
            job = dice.read_job(self.work, card["url"])
            job["id"] = card["id"]
            job["url"] = card["url"]
            job["company"] = job.get("company") or card["company"]
            job["title"] = job.get("title") or card["title"]
            job["salary"] = job.get("salary") or card.get("salary", "")
            say(f"{job['title']} @ {job['company']} ({job['location']}; {job['posted'] or card['posted']})")
            if job.get("applied"):
                return self._end(card, job, row, "skipped", "already applied", steps)
            if dice.age_days(card.get("posted", "")) is None:
                age = dice.job_age(job)
                if age is None or age > int(self.s["max_age_days"]) - 1:
                    return self._end(card, job, row, "skipped", f"date '{job.get('posted') or 'not shown'}' (outside {self.s['max_age_days']} day(s))", steps)
            ok, why = dice.check_remote(job, bool(self.s["strict_remote"]))
            if not ok:
                return self._end(card, job, row, "skipped", f"not remote: {why}", steps)
            say(f"  {why}")
            if not job.get("canApply") or not dice.apply_button(self.work):
                return self._end(card, job, row, "skipped", "no Easy Apply button", steps)
            if len(job.get("jd", "")) < 300:
                return self._end(card, job, row, "skipped", "could not read the job description", steps)

            # resume + cover letter
            row["status"] = "writing"
            self.ui.job(row)
            docs = self._documents(job, say)
            row.update(ats=f"{docs['ats']}%", risk=f"{docs['risk']}%")
            self.ui.job(row)

            # Easy Apply
            row["status"] = "applying"
            self.ui.job(row)
            self.work.bring_to_front()
            try:
                wiz = dice.open_wizard(self.work, self.browser.ctx, card["id"])
            except dice.External as e:
                try:
                    e.page.close()
                except Exception:
                    pass
                return self._end(card, job, row, "skipped", str(e), steps, docs)
            self.work = wiz
            return self._wizard(card, job, row, docs, steps, say)
        except dice.NotLoggedIn:
            raise
        except LLMError as e:
            if "Stopped" in str(e):
                return self._end(card, {"url": card["url"], **card}, row, "stopped", "stopped", steps)
            return self._end(card, {"url": card["url"], **card}, row, "failed", str(e), steps, debug=True)
        except Exception as e:
            self.log(traceback.format_exc(limit=3), "dim")
            return self._end(card, {"url": card["url"], **card}, row, "failed", f"{type(e).__name__}: {str(e)[:200]}", steps, debug=True)

    def _documents(self, job: dict, say) -> dict:
        s, p = self.s, self.profile
        target = int(s["ats_target"])
        jobin = {"title": job["title"], "company": job["company"], "location": job.get("location", ""), "workMode": "Remote", "salary": job.get("salary", ""), "jd": job["jd"]}
        chat = Chat(s, self.browser, lambda m, l="info": self.log(m, l), self.schema, self.stop.is_set)
        say(f"  writing the resume with {s['engine']}…")
        prompt = engine.call("prompt", {"profile": p, "job": jobin, "atsTarget": target})["prompt"]
        m = engine.call("merge", {"profile": p, "answer": chat.send(prompt), "job": jobin})
        say(f"  ATS {m['ats']['score']}%, consistency risk {m['val']['risk']}%")
        for r in range(int(s["fix_rounds"])):
            fix = engine.call("fix", {"val": m["val"], "ats": m["ats"]})
            if m["ats"]["score"] >= target and not fix["needed"]:
                break
            say(f"  correction round {r + 1}…")
            m2 = engine.call("merge", {"profile": p, "answer": chat.send(fix["prompt"]), "job": jobin})
            # keep the better of the two (lower risk first, then higher ATS)
            if (m2["val"]["risk"], -m2["ats"]["score"]) <= (m["val"]["risk"], -m["ats"]["score"]):
                m = m2
            say(f"  ATS {m['ats']['score']}%, consistency risk {m['val']['risk']}%")
        high = [x["msg"] for x in m["val"]["issues"] if x["severity"] == "high"]
        if high:
            raise RuntimeError("the resume still has serious consistency problems: " + "; ".join(high[:3]))
        chat.close(keep_tab=bool(s.get("keep_chat_tab", True)))

        letter = ""
        if s.get("cover_letter"):
            say("  writing the cover letter…")
            c = Chat(s, self.browser, lambda m_, l="info": self.log(m_, l), None, self.stop.is_set)
            cp = engine.call("cover_prompt", {"profile": p, "resume": m["resume"], "job": jobin})["prompt"]
            letter = engine.call("clean_letter", {"text": c.send(cp, json_mode=False)})["text"]
            c.close(keep_tab=bool(s.get("keep_chat_tab", True)))

        folder = self._folder(job)
        who = person_slug(p)
        files = engine.call("docx", {
            "resume": m["resume"], "letter": letter, "job": jobin, "template": s.get("template") or "professional",
            "resumePath": str(folder / f"{who}_cv.docx"), "coverPath": str(folder / f"{who}_cover_letter.docx")
        })
        (folder / "resume.json").write_text(json.dumps(m["resume"], indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "job.txt").write_text("\n".join([
            f"Company:  {job['company']}", f"Role:     {job['title']}", f"Link:     {job['url']}", f"Location: {job.get('location', '')}",
            f"Salary:   {job.get('salary', '')}", f"Posted:   {job.get('posted', '')}", f"Profile:  {p['name']}",
            f"ATS:      {m['ats']['score']}%   Consistency risk: {m['val']['risk']}%", "", job["jd"]]), encoding="utf-8")
        if letter:
            (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
        say(f"  files: {folder}")
        return {"resume": m["resume"], "letter": letter, "folder": str(folder), "resume_path": files["resume"],
                "cover_path": files.get("cover", ""), "ats": m["ats"]["score"], "risk": m["val"]["risk"]}

    def _folder(self, job) -> Path:
        base = Path(self.s["output_dir"]).expanduser() / datetime.now().strftime("%Y-%m-%d")
        name = slug(job["company"], 60)
        folder = base / name
        n = 2
        while folder.exists() and (folder / "job.txt").exists() and job["url"] not in (folder / "job.txt").read_text(encoding="utf-8", errors="ignore"):
            folder = base / f"{name}_{n}"
            n += 1
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _wizard(self, card, job, row, docs, steps, say) -> str:
        page = self.work
        page.wait_for_load_state("domcontentloaded")
        page.get_by_text(re.compile(r"resume", re.I)).first.wait_for(timeout=20000)
        time.sleep(1)
        say("  wizard: resume and cover letter")
        dice._upload_into(page, "Resume", "resume-card", docs["resume_path"], say)
        if docs.get("cover_path"):
            dice._upload_into(page, "Cover letter", "cover-card", docs["cover_path"], say)
        for step in range(6):
            if self.stop.is_set():
                return self._end(card, job, row, "stopped", "stopped in the wizard (tab left open)", steps, docs)
            if dice.is_review(page):
                break
            dice.click_named(page, re.compile(r"^\s*(next|continue)\s*$", re.I))
            page.wait_for_timeout(2500)
            if dice.is_review(page):
                break
            # an extra step with questions
            left = dice.answer_extra(page, engine.call, self.profile["personal"], docs.get("letter", ""), job.get("salary", ""), say)
            if left:
                return self._end(card, job, row, "attention", f"Dice asks for more: {'; '.join(left[:4])} (tab left open)", steps, docs, keep_tab=True)
        else:
            return self._end(card, job, row, "attention", "never reached the review step (tab left open)", steps, docs, keep_tab=True)

        review = dice.page_text(page)
        if Path(docs["resume_path"]).name not in review:
            return self._end(card, job, row, "attention", "the review step does not show our resume (tab left open)", steps, docs, keep_tab=True)
        if self.s["dry_run"]:
            return self._end(card, job, row, "attention", "dry run: stopped before Submit (tab left open)", steps, docs, keep_tab=True)
        say("  review OK: submitting")
        dice.click_named(page, re.compile(r"^\s*submit( application)?\s*$", re.I))
        end = time.time() + 30
        while time.time() < end and not dice.succeeded(page):
            time.sleep(1)
        if not dice.succeeded(page):
            # Dice may show the error or another question after Submit
            return self._end(card, job, row, "attention", "no confirmation after Submit (tab left open)", steps, docs, keep_tab=True, debug=True)
        job["apply_url"] = page.url
        return self._end(card, job, row, "applied", "submitted", steps, docs)

    # ---------- results ----------
    def _end(self, card, job, row, status, why, steps, docs=None, keep_tab=False, debug=False) -> str:
        level = {"applied": "ok", "attention": "warn", "failed": "err"}.get(status, "dim")
        self.log(f"→ {status.upper()}: {card['title'][:60]} @ {card['company'][:30]}: {why}", level)
        row["status"] = {"attention": "needs you"}.get(status, status)
        self.ui.job(row)
        if debug:
            self._debug(card)
        entry = {"status": status, "job_id": card["id"], "url": card["url"], "company": job.get("company") or card["company"],
                 "title": job.get("title") or card["title"], "why": why, "folder": (docs or {}).get("folder", ""),
                 "ats": (docs or {}).get("ats"), "risk": (docs or {}).get("risk"), "source": "dice"}
        if status != "stopped":
            ledger_add(entry)
        if status == "applied" and self.sb.ready:
            try:
                saved = self.sb.insert_bid(bid_row(
                    job=job, source="dice", resume=docs["resume"], letter=docs.get("letter", ""), folder=docs["folder"],
                    resume_path=docs["resume_path"], cover_path=docs.get("cover_path", ""), log=steps,
                    notes=f"Dice auto bid · profile {self.profile['name']} · ATS {docs['ats']}% · risk {docs['risk']}%",
                    engine=self.s["engine"], model=self._model(), apply_url=job.get("apply_url", "")))
                self.log(f"  saved to Supabase (bid #{saved.get('id', '?')})", "ok")
            except SupabaseError as e:
                self.log(f"  could not save to Supabase: {e}", "err")
        if status in ("attention", "failed") and keep_tab:
            self.work = None  # leave this tab; the next job opens a new one
        return status

    def _model(self) -> str:
        return {"openai": self.s.get("openai_model"), "deepseek_api": self.s.get("deepseek_model"),
                "chatgpt_chat": "chatgpt web chat"}.get(self.s["engine"], "deepseek web chat")

    def _debug(self, card):
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            stem = DEBUG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{card['id'][:8]}"
            if self.work and not self.work.is_closed():
                self.work.screenshot(path=str(stem) + ".png", full_page=True)
                Path(str(stem) + ".html").write_text(self.work.content(), encoding="utf-8")
                self.log(f"  debug saved: {stem}.png / .html", "dim")
        except Exception:
            pass
