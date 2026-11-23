"""AutoBid - Ashbyhq desktop app.

    python app.py        (or double-click AutoBid-Ashbyhq.bat)

Tabs:
  Jobs     - every collected job; pick one to see its Overview, the required questions
             with the answers the bot will use, and its tailored resume.
  Answers  - your profile + the answer bank (each required question answered once).
  Run      - collect jobs, make resumes, apply in batch mode (with a dry run), live log.

Long tasks run the existing scripts (collect.py, make_resume.py, bid.py) as a child
process, so the window never freezes and Stop always works.
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from config import BASE_DIR, BATCH_SIZE, STOP_FLAG, PAUSE_FLAG  # first: puts AutoBid-Python/common on sys.path
import ai_settings
import answers as bank_mod
import db

STATUS_COLORS = {"applied": "#1a7f37", "needs_you": "#b35900", "failed": "#c62828",
                 "skipped": "#777777", "ready": "#1f4fbf", "new": "#555555",
                 "1Interview": "#7b1fa2", "2Interview": "#6a1b9a", "3Interview": "#4a148c",
                 "Offer": "#00796b", "Rejected": "#8d6e63"}
PY = sys.executable.replace("pythonw.exe", "python.exe")
SETTINGS_PATH = BASE_DIR / "data" / "app_settings.json"   # also read by bid.py --selected

# "Posted" filter: label -> maximum age in days (None = no limit)
POSTED_WITHIN = {"all": None, "today": 0, "3d ago": 3, "1w ago": 7, "1m ago": 30}

# Auto-collect clock (US Eastern). Fixed times so "next" does not drift with run length.
AUTO_COLLECT_HOURS_ET = (1, 4, 8, 10, 12, 14, 16, 18, 20)
AUTO_SLOTS_LABEL = "1:00 · 4:00 · 8:00 · 10:00 · 12:00 · 14:00 · 16:00 · 18:00 · 20:00 ET"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone(timedelta(hours=-5))  # EST fallback if tzdata missing


def posted_date(value):
    """'2026-11-13' or '2024-03-04T14:29:08.532+00:00' -> date (None if missing)."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def job_fresh_date(row):
    """Date used for the Posted column and filter.

    Ashby's published_at is often a day (or more) older than when the job first appeared
    on the board for us. Jobs found by today's scrape must still count as 'today', so we
    take the later of published_at and first_seen_at."""
    dates = [d for d in (posted_date(row["published_at"]), posted_date(row["first_seen_at"])) if d]
    return max(dates) if dates else None


def posted_label(d, today):
    """today / yesterday / 5d ago / 3w ago / 4mo ago / 2y ago"""
    if not d:
        return ""
    days = (today - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days}d ago"
    if days < 60:
        return f"{days // 7}w ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _now_et():
    return datetime.now(ET)


def _slot_at(day, hour):
    """Eastern datetime for a schedule hour on the given Eastern calendar day."""
    return datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=ET)


def next_auto_slot(after=None):
    """Next fixed Eastern slot strictly after `after` (default: now)."""
    after = after or _now_et()
    if after.tzinfo is None:
        after = after.replace(tzinfo=ET)
    else:
        after = after.astimezone(ET)
    day = after.date()
    for _ in range(3):  # today / tomorrow / day after
        for h in AUTO_COLLECT_HOURS_ET:
            slot = _slot_at(day, h)
            if slot > after:
                return slot
        day += timedelta(days=1)
    return _slot_at(day, AUTO_COLLECT_HOURS_ET[0])


def latest_passed_slot(now=None):
    """Most recent schedule slot that is already due (≤ now Eastern)."""
    now = now or _now_et()
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)
    day = now.date()
    for h in reversed(AUTO_COLLECT_HOURS_ET):
        slot = _slot_at(day, h)
        if slot <= now:
            return slot
    # before today's first slot → yesterday's last
    yday = day - timedelta(days=1)
    return _slot_at(yday, AUTO_COLLECT_HOURS_ET[-1])


def parse_slot(iso):
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def load_settings():
    try:
        s = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("companies", [])          # selected companies; empty = all
    s.setdefault("auto_collect", False)
    s.setdefault("last_collect", None)     # ISO time of the last finished collection
    s.setdefault("auto_last_slot", None)   # ISO of the Eastern slot last started/covered
    if "auto_kind" not in s:
        # Auto-collection on by default: today's jobs at the fixed Eastern clock times.
        s.update(auto_collect=True, auto_kind="today", auto_last_slot=None)
        save_settings(s)
    s.setdefault("auto_kind", "today")
    # Migrate old "every N hours from last start" → fixed Eastern slots (one-time).
    if s.get("auto_started") and not s.get("auto_last_slot"):
        started = parse_slot(s["auto_started"])
        if started:
            s["auto_last_slot"] = latest_passed_slot(started).isoformat()
            save_settings(s)
    return s


def save_settings(s):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")


class CompanyPicker(tk.Toplevel):
    """Multi-select list of companies with search, Select all / Clear."""

    def __init__(self, master, counts, selected, on_apply):
        super().__init__(master)
        self.title("Companies")
        self.geometry("460x560")
        self.transient(master)
        self.grab_set()
        self.counts = counts                      # [(name, n jobs)]
        self.chosen = set(selected) or {n for n, _ in counts}
        self.on_apply = on_apply

        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Search").pack(side="left")
        self.q = ttk.Entry(top)
        self.q.pack(side="left", fill="x", expand=True, padx=6)
        self.q.bind("<KeyRelease>", lambda e: self.fill())
        self.q.focus_set()

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        self.lb = tk.Listbox(mid, selectmode="multiple", exportselection=False, activestyle="none",
                             selectbackground="#1f4fbf", font=("Segoe UI", 10))
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(side="left", fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", lambda e: self.sync())

        bot = ttk.Frame(self, padding=10)
        bot.pack(fill="x")
        ttk.Button(bot, text="Select all", command=lambda: self.set_visible(True)).pack(side="left")
        ttk.Button(bot, text="Clear", command=lambda: self.set_visible(False)).pack(side="left", padx=6)
        self.info = ttk.Label(bot, text="", style="Stat.TLabel")
        self.info.pack(side="left", padx=6)
        ttk.Button(bot, text="Apply", style="Accent.TButton", command=self.apply).pack(side="right")
        ttk.Button(bot, text="Cancel", command=self.destroy).pack(side="right", padx=6)
        self.fill()

    def fill(self):
        term = self.q.get().strip().lower()
        self.visible = [n for n, _ in self.counts if term in n.lower()]
        cnt = dict(self.counts)
        self.lb.delete(0, "end")
        for i, n in enumerate(self.visible):
            self.lb.insert("end", f"{n}  ({cnt[n]})")
            if n in self.chosen:
                self.lb.selection_set(i)
        self._info()

    def sync(self):
        sel = set(self.lb.curselection())
        for i, n in enumerate(self.visible):
            (self.chosen.add if i in sel else self.chosen.discard)(n)
        self._info()

    def set_visible(self, on):
        for n in self.visible:
            (self.chosen.add if on else self.chosen.discard)(n)
        self.fill()

    def _info(self):
        self.info.config(text=f"{len(self.chosen)} of {len(self.counts)} selected")

    def apply(self):
        everything = len(self.chosen) == len(self.counts)
        self.on_apply([] if everything else sorted(self.chosen))
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBid - Ashbyhq")
        self.geometry("1280x820")
        self.minsize(1000, 640)
        self.conn = db.connect()
        try:
            self.bank = bank_mod.load_bank()            # answer bank + profile live in Supabase
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception as e:
            self.withdraw()
            messagebox.showerror("AutoBid - Supabase", f"The answer bank could not be loaded from Supabase:\n\n{e}")
            raise SystemExit(1)
        self.proc = None
        self.proc_label = ""
        self._pending_collect = None   # "today" | "all" queued while Apply/etc. is running
        self.settings = load_settings()
        self.resolved = {}
        self.out_q = queue.Queue()
        self.ui_q = queue.Queue()      # work from background threads -> main thread
        self.selected_job = None
        self.selected_q = None

        self._style()
        self._header()
        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs = ttk.Notebook(body)
        body.add(self.tabs, weight=4)
        self._jobs_tab()
        self._answers_tab()
        self._run_tab()
        self._ai_tab()
        body.add(self._log_panel(), weight=1)

        self.refresh_all()
        self._auto_info()
        self.after(150, self._drain_output)
        self.after(400, self._reopen_false_negatives)  # Data/IT/Solutions wrongly marked no
        self.after(10_000, self._auto_tick)       # first auto-collect check 10 s after start

    # ------------------------------------------------------------ look

    def _style(self):
        s = ttk.Style(self)
        if "vista" in s.theme_names():
            s.theme_use("vista")
        self.option_add("*Font", ("Segoe UI", 10))
        s.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        s.configure("Treeview.Heading", font=("Segoe UI Semibold", 10))
        s.configure("Title.TLabel", font=("Segoe UI Semibold", 16))
        s.configure("Stat.TLabel", font=("Segoe UI", 10), foreground="#444")
        s.configure("H.TLabel", font=("Segoe UI Semibold", 11))
        s.configure("Accent.TButton", font=("Segoe UI Semibold", 10))

    def _header(self):
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="AutoBid · Ashbyhq", style="Title.TLabel").pack(side="left")
        ttk.Label(top, text="  ashbyhq.com/careers · Remote", style="Stat.TLabel").pack(side="left")
        ttk.Button(top, text="Open bot browser", command=self.open_bot_browser).pack(
            side="left", padx=(16, 0))
        self.stats = ttk.Label(top, text="", style="Stat.TLabel")
        self.stats.pack(side="right")
        self.auto_hdr = ttk.Label(top, text="", style="Stat.TLabel", foreground="#1a7f37")
        self.auto_hdr.pack(side="right", padx=16)

        # Run bar: visible on every tab while a task (apply, resumes, collection) is running
        self.run_bar = tk.Frame(self, bg="#fff4e5", highlightthickness=1, highlightbackground="#f0c27a")
        self.run_label = tk.Label(self.run_bar, text="", bg="#fff4e5", fg="#7a4a00",
                                  font=("Segoe UI Semibold", 10), anchor="w")
        self.run_label.pack(side="left", padx=(12, 8), pady=6)
        # Live apply counters (this run only): success / failed / need you
        self.run_stats = {"ok": 0, "fail": 0, "need": 0}
        self.run_ok = tk.Label(self.run_bar, text="", bg="#fff4e5", fg="#1a7f37",
                               font=("Segoe UI Semibold", 10))
        self.run_fail = tk.Label(self.run_bar, text="", bg="#fff4e5", fg="#c62828",
                                 font=("Segoe UI Semibold", 10))
        self.run_need = tk.Label(self.run_bar, text="", bg="#fff4e5", fg="#b35900",
                                 font=("Segoe UI Semibold", 10))
        self.run_ok.pack(side="left", padx=(16, 4), pady=6)
        self.run_fail.pack(side="left", padx=(4, 4), pady=6)
        self.run_need.pack(side="left", padx=(4, 4), pady=6)
        # spacer so control buttons stay on the right
        tk.Label(self.run_bar, text="", bg="#fff4e5").pack(side="left", fill="x", expand=True)
        # pack order with side=right: first = rightmost
        self.b_stop_now = tk.Button(self.run_bar, text="■  Stop now", command=self.stop,
                                    bg="#c62828", fg="white", activebackground="#a51f1f",
                                    activeforeground="white", relief="flat", padx=14, pady=3,
                                    font=("Segoe UI Semibold", 10), cursor="hand2")
        self.b_stop_now.pack(side="right", padx=(6, 10), pady=4)
        self.b_pause = tk.Button(self.run_bar, text="⏸  Pause", command=self.toggle_pause,
                                 bg="#ffffff", fg="#7a4a00", relief="solid", bd=1, padx=12, pady=2,
                                 font=("Segoe UI Semibold", 10), cursor="hand2")
        self.b_pause.pack(side="right", padx=(6, 0), pady=4)
        self.b_stop_after = tk.Button(self.run_bar, text="Stop after this job", command=self.stop_after,
                                      bg="#ffffff", fg="#7a4a00", relief="solid", bd=1, padx=12, pady=2,
                                      font=("Segoe UI", 10), cursor="hand2")
        self.b_stop_after.pack(side="right", pady=4)
        self._header_frame = top

    # ------------------------------------------------------------ Jobs tab

    def _jobs_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="  Jobs  ")
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Show").pack(side="left")
        self.f_relevant = ttk.Combobox(bar, width=13, state="readonly",
                                       values=["relevant", "not relevant", "all"])
        self.f_relevant.set("relevant")
        self.f_relevant.pack(side="left", padx=(4, 12))
        self.f_relevant.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Status").pack(side="left")
        self.f_status = ttk.Combobox(bar, width=12, state="readonly",
                                     values=["all", "applied + later"] + db.STATUSES)
        self.f_status.set("all")
        self.f_status.pack(side="left", padx=(4, 12))
        self.f_status.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        self.company_btn = ttk.Button(bar, text="Companies: all ▾", width=24, command=self.pick_companies)
        self.company_btn.pack(side="left", padx=(0, 12))
        ttk.Label(bar, text="Posted").pack(side="left")
        self.f_posted = ttk.Combobox(bar, width=9, state="readonly", values=list(POSTED_WITHIN))
        self.f_posted.set("today")   # default: today's jobs are easy to find
        self.f_posted.pack(side="left", padx=(4, 6))
        self.f_posted.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Button(bar, text="Today's jobs", style="Accent.TButton",
                   command=self.show_todays_jobs).pack(side="left", padx=(0, 12))

        bar = ttk.Frame(tab)                       # second filter row
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Answers").pack(side="left")
        self.f_answers = ttk.Combobox(bar, width=28, state="readonly", values=[
            "any", "✓ all (ready to apply)", "missing answers", "AI essay needed", "no details yet"])
        # "✓ all (ready to apply)" includes jobs whose only gaps are AI-written essays
        self.f_answers.set("any")
        self.f_answers.pack(side="left", padx=(4, 12))
        self.f_answers.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Resume").pack(side="left")
        self.f_resume = ttk.Combobox(bar, width=13, state="readonly", values=["any", "✓ ready", "not made yet"])
        self.f_resume.set("any")
        self.f_resume.pack(side="left", padx=(4, 12))
        self.f_resume.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Search").pack(side="left")
        self.f_search = ttk.Entry(bar, width=30)
        self.f_search.pack(side="left", padx=4)
        self.f_search.bind("<KeyRelease>", lambda e: self.load_jobs())
        self.job_count = ttk.Label(bar, text="", style="Stat.TLabel")
        self.job_count.pack(side="left", padx=12)
        ttk.Button(bar, text="⬆ Sync to DB", command=self.sync_to_db).pack(side="right")
        ttk.Button(bar, text="⬇ Sync from DB", command=self.sync_from_db).pack(side="right", padx=(4, 4))
        ttk.Button(bar, text="Fetch missing details", command=self.fetch_missing_details).pack(side="right", padx=4)
        ttk.Button(bar, text="Collect today's jobs", command=self.collect_today).pack(side="right", padx=4)
        ttk.Button(bar, text="⟳ Collect jobs", command=self.collect).pack(side="right")

        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)

        left = ttk.Frame(split)
        cols = ("chk", "status", "company", "title", "posted", "dept", "location", "pay", "ready", "resume", "rel")
        # The ticked jobs (☑) are the Treeview's selection; clicking a row only SHOWS it.
        self.jobs = ttk.Treeview(left, columns=cols, show="headings", selectmode="extended")
        self.jobs.bind("<Control-a>", lambda e: (self.select_all_jobs(), "break")[1])
        self.jobs.heading("chk", text="☐", command=self.toggle_all_shown)
        self.jobs.column("chk", width=34, minwidth=34, anchor="center", stretch=False)
        self.jobs.tag_configure("current", background="#fff4c2")
        self.jobs.bind("<Button-1>", self._on_job_click)
        self.jobs.bind("<space>", lambda e: (self._toggle(self.current_id), "break")[1])
        self.jobs.bind("<Up>", lambda e: (self._move_view(-1), "break")[1])
        self.jobs.bind("<Down>", lambda e: (self._move_view(1), "break")[1])
        self.current_id = None
        self._checked = set()
        self.job_headings = {}
        self.sort_col, self.sort_desc = None, False
        for c, label, w in [("status", "Status", 75), ("company", "Company", 90), ("title", "Title", 240),
                            ("posted", "Posted", 80), ("dept", "Department", 115), ("location", "Location", 130), ("pay", "Pay", 150),
                            ("ready", "Answers", 80), ("resume", "Resume", 55), ("rel", "Relevant", 75)]:
            self.job_headings[c] = label
            self.jobs.heading(c, text=label, command=lambda c=c: self.sort_jobs(c, toggle=True))
            self.jobs.column(c, width=w, anchor="w", stretch=c in ("title", "location"))
        for st, col in STATUS_COLORS.items():
            self.jobs.tag_configure(st, foreground=col)
        # grid, not pack: the scrollbars always keep their place, however wide the columns are
        vsb = ttk.Scrollbar(left, orient="vertical", command=self.jobs.yview)
        hsb = ttk.Scrollbar(left, orient="horizontal", command=self.jobs.xview)
        self.jobs.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.jobs.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.jobs.bind("<<TreeviewSelect>>", lambda e: self._sync_checks())
        split.add(left, weight=3)

        right = ttk.Frame(split, padding=(10, 0, 0, 0))
        self.d_title = ttk.Label(right, text="Select a job", style="H.TLabel", wraplength=480)
        self.d_title.pack(anchor="w")
        self.d_meta = ttk.Label(right, text="", style="Stat.TLabel", wraplength=480, justify="left")
        self.d_meta.pack(anchor="w", pady=(2, 6))
        selbar = ttk.Frame(right)
        selbar.pack(fill="x", pady=(0, 4))
        # buttons packed FIRST so they always keep their full size; the hint gets the rest
        # of the row and wraps to a second line when the panel is narrow
        ttk.Button(selbar, text="Select all shown", command=self.select_all_jobs).pack(side="right")
        ttk.Button(selbar, text="Clear selection", command=self.clear_job_selection).pack(side="right", padx=4)
        self.sel_info = ttk.Label(selbar, text="", foreground="#1f4fbf", justify="left")
        self.sel_info.pack(side="left", fill="x", expand=True)
        self.sel_info.bind("<Configure>", lambda e: self.sel_info.config(wraplength=max(120, e.width - 4)))
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(0, 6))
        ttk.Button(btns, text="Open job page", command=self.open_job).pack(side="left")
        self.b_make = ttk.Button(btns, text="Make resume", command=self.make_resume_selected)
        self.b_make.pack(side="left", padx=4)
        ttk.Button(btns, text="Open resume", command=self.open_resume).pack(side="left")
        self.b_dry = ttk.Button(btns, text="Dry run", command=self.dry_run_selected)
        self.b_dry.pack(side="left", padx=4)
        self.b_apply = ttk.Button(btns, text="Apply", style="Accent.TButton", command=self.apply_selected)
        self.b_apply.pack(side="left")
        self.b_answers_sel = ttk.Button(btns, text="Answers for selected",
                                        command=self.show_answers_for_selected)
        self.b_answers_sel.pack(side="left", padx=4)
        stbar = ttk.Frame(right)
        stbar.pack(fill="x", pady=(0, 6))
        ttk.Label(stbar, text="Status").pack(side="left")
        self.d_status = ttk.Combobox(stbar, width=12, state="readonly", values=db.STATUSES)
        self.d_status.pack(side="left", padx=4)
        self.b_status = ttk.Button(stbar, text="Set status", command=self.set_status_selected)
        self.b_status.pack(side="left")
        st_hint = ttk.Label(stbar, text="ticked jobs, else the one shown · saved to Supabase",
                            style="Stat.TLabel", justify="left")
        st_hint.pack(side="left", padx=6, fill="x", expand=True)
        st_hint.bind("<Configure>", lambda e: st_hint.config(wraplength=max(120, e.width - 4)))

        self.d_tabs = ttk.Notebook(right)
        self.d_tabs.pack(fill="both", expand=True)
        self.d_overview = ScrolledText(self.d_tabs, wrap="word", font=("Segoe UI", 10), padx=8, pady=6,
                                       relief="flat", borderwidth=0)
        self.d_tabs.add(self.d_overview, text="Overview")
        self.d_overview.tag_configure("h", font=("Segoe UI Semibold", 11), spacing1=8, spacing3=2)
        self.d_overview.tag_configure("b", font=("Segoe UI Semibold", 10))
        app_frame = ttk.Frame(self.d_tabs)
        self.d_app = ttk.Treeview(app_frame, columns=("q", "a", "src"), show="headings")
        for c, label, w in [("q", "Required question", 220), ("a", "Answer the bot will use", 170),
                            ("src", "From", 80)]:
            self.d_app.heading(c, text=label)
            self.d_app.column(c, width=w, minwidth=60, anchor="w", stretch=c != "src")
        self.d_app.tag_configure("missing", foreground="#c62828")
        self.d_app.tag_configure("ai", foreground="#b35900")
        ttk.Label(app_frame, text="Double-click a question to answer it in the Answers tab.",
                  style="Stat.TLabel").pack(side="bottom", anchor="w", pady=(4, 0))
        asb = ttk.Scrollbar(app_frame, orient="vertical", command=self.d_app.yview)
        self.d_app.configure(yscrollcommand=asb.set)
        asb.pack(side="right", fill="y")
        self.d_app.pack(side="left", fill="both", expand=True)
        self.d_app.bind("<Double-1>", self.answer_from_job)
        self.d_app.bind("<Return>", self.answer_from_job)
        self.d_app_items = {}
        self.d_tabs.add(app_frame, text="Application")
        split.add(right, weight=2)

    def load_jobs(self):
        status = self.f_status.get()
        term = self.f_search.get().strip().lower()
        sql = "SELECT * FROM jobs WHERE is_active=1"
        params = []
        show = self.f_relevant.get()
        if show == "relevant":
            sql += " AND relevant=1"
        elif show == "not relevant":
            sql += " AND relevant=0"
        if status == "applied + later":
            sql += f" AND status IN ({','.join('?' * len(db.APPLIED_STATUSES))})"
            params += db.APPLIED_STATUSES
        elif status != "all":
            sql += " AND status=?"
            params.append(status)
        chosen = self.settings["companies"]
        if chosen:
            sql += f" AND company IN ({','.join('?' * len(chosen))})"
            params += chosen
        rows = self.conn.execute(sql + " ORDER BY published_at DESC", params).fetchall()
        self.company_btn.config(text="Companies: all ▾" if not chosen else
                                (f"{chosen[0][:18]} ▾" if len(chosen) == 1 else f"Companies: {len(chosen)} ▾"))
        keep = self.jobs.selection()
        self.jobs.delete(*self.jobs.get_children())
        answers_filter = self.f_answers.get()
        max_age = POSTED_WITHIN[self.f_posted.get()]
        today = date.today()
        self.posted_dates = {}
        for r in rows:
            company = r["company"] or r["org"]
            if term and term not in f"{company} {r['title']} {r['department']} {r['location']}".lower():
                continue
            posted = job_fresh_date(r)
            if max_age is not None and (posted is None or (today - posted).days > max_age):
                continue
            self.posted_dates[r["id"]] = posted
            missing = bank_mod.blocking(self.resolved.get(r["id"], []))
            if not r["relevant"]:
                ready, state = "—", "none"
            elif r["id"] not in self.resolved:
                ready, state = "no details yet", "no details"
            elif not missing:
                ready, state = "✓ all", "all"
            elif all(bank_mod.ai_writable(i) for i in missing):
                # bid step writes these with AI - treat as apply-available
                ready, state = (f"AI writes ({len(missing)})", "essay")
            else:
                ready, state = f"{len(missing)} missing", "missing"
            if answers_filter != "any":
                want = {"✓ all (ready to apply)": "all", "missing answers": "missing",
                        "AI essay needed": "essay", "no details yet": "no details"}[answers_filter]
                # "ready to apply" also includes jobs whose only gaps the AI will write
                if want == "all":
                    if state not in ("all", "essay"):
                        continue
                elif want != state:
                    continue
            has_resume = "✓" if r["resume_path"] and os.path.isfile(r["resume_path"]) else ""
            resume_filter = self.f_resume.get()
            if (resume_filter == "✓ ready" and not has_resume) or (resume_filter == "not made yet" and has_resume):
                continue
            rel ={1: "yes", 0: "no"}.get(r["relevant"], "?") + (f" ({r['relevance_by']})" if r["relevance_by"] else "")
            self.jobs.insert("", "end", iid=r["id"], tags=(r["status"],), values=(
                "☐", r["status"], company, r["title"], posted_label(posted, today),
                r["team"] or r["department"], r["location"], r["compensation"], ready, has_resume, rel))
        if self.sort_col:
            self.sort_jobs(self.sort_col)
        shown = len(self.jobs.get_children())
        today_n = sum(1 for d in self.posted_dates.values() if d == today)
        # When Posted≠today, still hint how many "today" exist in the unfiltered relevant set
        if self.f_posted.get() == "today":
            self.job_count.config(text=f"{shown:,} today's jobs shown")
        else:
            self.job_count.config(text=f"{shown:,} shown · {today_n:,} today in this list")
        self._checked = set()
        still_there = [i for i in keep if self.jobs.exists(i)]   # keep the ticks
        if still_there:
            self.jobs.selection_set(still_there)
        if self.current_id and self.jobs.exists(self.current_id):
            self._mark_current(self.current_id)
        self._sync_checks()

    def pick_companies(self):
        """Multi-select company filter (also used by 'Start bidding' when 'only selected' is on)."""
        counts = [(r["company"], r["n"]) for r in self.conn.execute(
            "SELECT company, COUNT(*) n FROM jobs WHERE is_active=1 AND relevant=1 AND company IS NOT NULL "
            "GROUP BY company ORDER BY n DESC, company")]

        def apply(selection):
            self.settings["companies"] = selection
            save_settings(self.settings)
            self.load_jobs()
            self.update_summary()
        CompanyPicker(self, counts, self.settings["companies"], apply)

    @staticmethod
    def _sort_key(col, value):
        if col == "pay":   # "$145K – $186K ..." / "€78,000 – ..." -> 145000 / 78000
            m = re.search(r"(\d[\d,.]*)\s*([KkMm]?)", value or "")
            if not m:
                return -1
            n = float(m.group(1).replace(",", ""))
            return n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)
        if col == "ready":  # "✓ all" first, then by how many answers are missing
            return 0 if value.startswith("✓") else int(re.sub(r"\D", "", value) or 99)
        return (value or "").lower()

    def sort_jobs(self, col, toggle=False):
        """Click a column header to sort; click again to reverse."""
        if toggle:
            self.sort_desc = (not self.sort_desc) if self.sort_col == col else False
            self.sort_col = col
        if col == "posted":   # by the real date (newest = largest); jobs without a date last
            key = lambda i: (self.posted_dates.get(i) or date.min).toordinal()
        else:
            key = lambda i: self._sort_key(col, self.jobs.set(i, col))
        rows = [(key(i), i) for i in self.jobs.get_children()]
        rows.sort(reverse=self.sort_desc)
        for n, (_, iid) in enumerate(rows):
            self.jobs.move(iid, "", n)
        for c, label in self.job_headings.items():
            arrow = (" ▼" if self.sort_desc else " ▲") if c == col else ""
            self.jobs.heading(c, text=label + arrow)

    # ---------------- check boxes (ticked jobs) + the job shown on the right

    def selected_ids(self):
        """Ticked jobs; with none ticked, the job shown on the right."""
        ticked = list(self.jobs.selection())
        if ticked:
            return ticked
        return [self.current_id] if self.current_id and self.jobs.exists(self.current_id) else []

    def select_all_jobs(self):
        self.jobs.selection_set(self.jobs.get_children())

    def clear_job_selection(self):
        self.jobs.selection_remove(self.jobs.selection())

    def toggle_all_shown(self):
        """Header ☐: tick every job in the (filtered) list, or untick all."""
        rows = self.jobs.get_children()
        if rows and len(self.jobs.selection()) == len(rows):
            self.clear_job_selection()
        else:
            self.select_all_jobs()

    def _toggle(self, row):
        if row and self.jobs.exists(row):
            self.jobs.selection_toggle(row)

    def _on_job_click(self, event):
        if self.jobs.identify_region(event.x, event.y) == "heading":
            return None                                # sorting / header checkbox
        row = self.jobs.identify_row(event.y)
        if not row:
            return None
        if event.state & 0x0005:                       # Ctrl / Shift: classic multi-select
            self.after(1, lambda: self.view_job(row))
            return None
        if self.jobs.identify_column(event.x) == "#1":  # the ☐ box: tick / untick only this row
            self._toggle(row)                          # (the job shown on the right stays)
            if not self.current_id:
                self.view_job(row)
            return "break"
        self.view_job(row)                             # a click on the row shows the job
        return "break"                                 # ...without clearing the ticks

    def _move_view(self, step):
        rows = self.jobs.get_children()
        if not rows:
            return
        i = rows.index(self.current_id) + step if self.current_id in rows else 0
        self.view_job(rows[max(0, min(len(rows) - 1, i))])

    def _mark_current(self, row):
        old = self.current_id
        if old and self.jobs.exists(old):
            self.jobs.item(old, tags=tuple(t for t in self.jobs.item(old, "tags") if t != "current"))
        self.current_id = row
        self.jobs.item(row, tags=tuple(self.jobs.item(row, "tags")) + ("current",))

    def view_job(self, row):
        self._mark_current(row)
        self.jobs.focus(row)
        self.jobs.see(row)
        self.show_job(row)

    def _sync_checks(self):
        """Draw ☑/☐ for rows whose tick changed, update header + buttons."""
        now = set(self.jobs.selection())
        for row in now ^ self._checked:
            if self.jobs.exists(row):
                self.jobs.set(row, "chk", "☑" if row in now else "☐")
        self._checked = now
        rows = len(self.jobs.get_children())
        self.jobs.heading("chk", text="☑" if rows and len(now) == rows else ("◪" if now else "☐"))
        self._update_selection_buttons()

    def _update_selection_buttons(self):
        n = len(self.jobs.selection())
        suffix = f" ({n})" if n > 1 else ""
        self.b_make.config(text=f"Make resume{suffix}")
        self.b_dry.config(text=f"Dry run{suffix}")
        self.b_apply.config(text=f"Apply{suffix}")
        self.b_answers_sel.config(text=f"Answers for selected{suffix}")
        self.sel_info.config(text=f"☑ {n} job{'s' if n != 1 else ''} ticked - the buttons work on "
                                  f"{'them' if n != 1 else 'it'}" if n else
                             "Tick ☐ jobs to work on several at once (header ☐ = all shown)")

    def _write_selection(self, ids):
        """The selected job ids go to the scripts in a file (the command line would get too long)."""
        path = BASE_DIR / "data" / "selected_jobs.json"
        path.write_text(json.dumps(ids), encoding="utf-8")
        return str(path)

    def show_job(self, shown=None):
        """Details of one job on the right: the given one, else the one being viewed."""
        shown = shown or self.current_id
        if not shown or not self.jobs.exists(shown):
            return
        job = self.conn.execute("SELECT * FROM jobs WHERE id=?", (shown,)).fetchone()
        if not job:
            return
        self.selected_job = job
        self.d_status.set(job["status"] or "")
        self.d_title.config(text=f"{job['title']}  ·  {job['company'] or job['org']}")
        tiers = (job["compensation_tiers"] or job["compensation"] or "").replace("\n", "   ·   ")
        meta = f"{job['department']} › {job['team']}  ·  {job['employment_type']}  ·  {job['workplace_type']}\n" \
               f"{', '.join(json.loads(job['locations_all'] or '[]'))}\n{tiers}\n" \
               f"Status: {job['status']}" + (f"  —  {job['note']}" if job["note"] else "")
        self.d_meta.config(text=meta)

        t = self.d_overview
        t.config(state="normal")
        t.delete("1.0", "end")
        for line in (job["description"] or "").splitlines():
            if line.startswith("#"):
                t.insert("end", line.lstrip("# ") + "\n", "h")
                continue
            parts = line.split("**")
            for i, part in enumerate(parts):
                t.insert("end", part, "b" if i % 2 else "")
            t.insert("end", "\n")
        t.config(state="disabled")

        self.d_app.delete(*self.d_app.get_children())
        self.d_app_items = {}
        for i in bank_mod.resolve_job(self.conn, job["id"], self.bank):
            q = i["q"]
            self.d_app_items[q["id"]] = i
            if i["error"]:
                ans, tag = f"!! {i['error']}", "missing"
            elif i["missing"]:
                if bank_mod.ai_writable(i):
                    ans, tag = "(AI will write)", "ai"
                elif (i["source"] or "").startswith("profile"):
                    ans, tag = "MISSING - fill My profile", "missing"
                else:
                    ans, tag = "MISSING - answer in Answers tab", "missing"
            else:
                v = i["value"]
                ans = ("Yes" if v else "No") if isinstance(v, bool) else str(v)
                tag = ""
            self.d_app.insert("", "end", iid=q["id"], values=(q["title"], ans[:200], i["source"] or ""),
                              tags=(tag,))

    def answer_from_job(self, _event=None):
        """Double-click on a question in Jobs > Application: open it in the Answers tab."""
        sel = self.d_app.selection()
        item = self.d_app_items.get(sel[0]) if sel else None
        if not item:
            return
        q, source = item["q"], item["source"] or ""
        if source.startswith("tailored"):
            messagebox.showinfo("Resume", "The resume is written for each job by the AI "
                                          "(Make resume / bidding). Nothing to answer here.")
            return
        self.return_to_job = self.selected_job["id"] if self.selected_job else None
        self.back_btn.pack(side="left")
        self.tabs.select(self.answers_tab)

        if source.startswith("profile."):              # Name, Email, Location, LinkedIn, ...
            entry = self.p_entries.get(source.split(".", 1)[1])
            if entry:
                entry.focus_set()
                entry.select_range(0, "end")
                self.e_question.config(text=f"\"{q['title']}\" comes from My profile (left) - "
                                            f"fill it there and click Save profile.")
                self.e_note.config(text="")
            return

        idx = next((n for n, b in enumerate(self.bank["questions"]) if b["key"] == q["bank_key"]), None)
        if idx is None:                                 # not in the bank yet: add it
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
            idx = next((n for n, b in enumerate(self.bank["questions"]) if b["key"] == q["bank_key"]), None)
            if idx is None:
                messagebox.showwarning("Answers", "This question is not in the answer bank.")
                return
        if not self.qlist.exists(str(idx)):             # hidden by a filter: show all required
            self.q_only_open.set(False)
            self.q_kind.set("All required")
            self.load_bank_list()
        self.qlist.selection_set(str(idx))              # -> edit_question() fills the Answer box
        self.qlist.see(str(idx))
        self.qlist.focus(str(idx))
        self.after(50, lambda: getattr(self, "e_widget", self.qlist).focus_set())

    def back_to_job(self):
        jid = getattr(self, "return_to_job", None)
        self.back_btn.pack_forget()
        self.tabs.select(0)
        self.d_tabs.select(1)                           # the job's Application tab
        if jid and self.jobs.exists(jid):
            self.view_job(jid)                          # show it again, ticks unchanged

    def open_job(self):
        if self.selected_job:
            webbrowser.open(self.selected_job["job_url"])

    def open_resume(self):
        job = self._fresh_job()
        if job and job["resume_path"] and os.path.isfile(job["resume_path"]):
            os.startfile(job["resume_path"])
        else:
            messagebox.showinfo("Resume", "No resume for this job yet. Click 'Make resume'.")

    def _fresh_job(self):
        if not self.selected_job:
            return None
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (self.selected_job["id"],)).fetchone()

    # ------------------------------------------------------------ Answers tab

    def _answers_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.answers_tab = tab
        self.tabs.add(tab, text="  Answers  ")
        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)

        prof = ttk.LabelFrame(split, text="  My profile  ", padding=10)
        self.p_entries = {}
        for row, (key, label) in enumerate([
                ("name", "Full name"), ("email", "Email"), ("phone", "Phone"),
                ("linkedin", "LinkedIn URL"), ("github", "GitHub URL"), ("website", "Website"),
                ("location", "City, State, Country"), ("resume_file", "Fallback resume PDF")]):
            ttk.Label(prof, text=label).grid(row=row, column=0, sticky="w", pady=3)
            e = ttk.Entry(prof, width=34)
            e.grid(row=row, column=1, sticky="ew", pady=3, padx=(8, 0))
            self.p_entries[key] = e
        prof.columnconfigure(1, weight=1)
        ttk.Label(prof, text="Location is typed into Ashby's place search,\n"
                             "e.g. \"Austin, Texas, United States\".",
                  style="Stat.TLabel").grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(prof, text="Save profile", style="Accent.TButton",
                   command=self.save_profile).grid(row=10, column=1, sticky="e", pady=(10, 0))
        self.p_saved = ttk.Label(prof, text="", foreground="#1a7f37")
        self.p_saved.grid(row=11, column=1, sticky="e")
        split.add(prof, weight=1)

        right = ttk.Frame(split, padding=(10, 0, 0, 0))
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Required questions (answer once, reused on every job)",
                  style="H.TLabel").pack(side="left")
        self.q_only_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="only unanswered", variable=self.q_only_open,
                        command=self.load_bank_list).pack(side="right")
        ttk.Button(top, text="Import from backup…",
                   command=self.import_answers_backup).pack(side="right", padx=(0, 8))
        self.q_kind = ttk.Combobox(top, state="readonly", width=24,
                                   values=["You answer", "AI writes (text / essays)", "All required"])
        self.q_kind.set("You answer")
        self.q_kind.bind("<<ComboboxSelected>>", lambda e: self.load_bank_list())
        self.q_kind.pack(side="right", padx=(0, 12))
        # Shown when opened from Jobs → "Answers for selected"
        self.answers_filter_ids = None          # set of job ids, or None = all jobs
        self.answers_filter_keys = None         # bank_key -> count among selected jobs
        self.q_filter_bar = ttk.Frame(right)
        self.q_filter_lbl = ttk.Label(self.q_filter_bar, text="", foreground="#1f4fbf")
        self.q_filter_lbl.pack(side="left")
        ttk.Button(self.q_filter_bar, text="Show all answers",
                   command=self.clear_answers_job_filter).pack(side="right")
        self.q_count = ttk.Label(right, text="", style="Stat.TLabel")
        self.q_count.pack(anchor="w")
        cols = ("jobs", "type", "question", "answer")
        qframe = ttk.Frame(right)   # packed below, after the Answer box (so the box always fits)
        self.qlist = ttk.Treeview(qframe, columns=cols, show="headings", selectmode="browse", height=12)
        for c, label, w in [("jobs", "Required in", 80), ("type", "Type", 110),
                            ("question", "Question", 420), ("answer", "Answer", 200)]:
            self.qlist.heading(c, text=label)
            self.qlist.column(c, width=w, anchor="w", stretch=c == "question")
        self.qlist.tag_configure("open", foreground="#c62828")
        qsb = ttk.Scrollbar(qframe, orient="vertical", command=self.qlist.yview)
        self.qlist.configure(yscrollcommand=qsb.set)
        qsb.pack(side="right", fill="y")
        self.qlist.pack(side="left", fill="both", expand=True)
        self.qlist.bind("<<TreeviewSelect>>", lambda e: self.edit_question())

        # Answer box docked at the bottom; the question list above takes the space left over.
        ed = ttk.LabelFrame(right, text="  Answer  ", padding=8)
        ed.pack(side="bottom", fill="x")
        eb = ttk.Frame(ed)                      # buttons first, so they are never pushed out
        eb.pack(side="bottom", fill="x")
        ttk.Button(eb, text="Save answer", style="Accent.TButton", command=self.save_answer).pack(side="right")
        ttk.Button(eb, text="Clear", command=lambda: self.save_answer(clear=True)).pack(side="right", padx=4)
        self.e_saved = ttk.Label(eb, text="", foreground="#1a7f37")
        self.e_saved.pack(side="right", padx=10)
        # shown only after coming here by double-clicking a question in Jobs > Application
        self.back_btn = ttk.Button(eb, text="← Back to job", command=self.back_to_job)
        self.e_question = ttk.Label(ed, text="Select a question above.", wraplength=760, justify="left")
        self.e_question.pack(anchor="w")
        self.e_note = ttk.Label(ed, text="", style="Stat.TLabel", wraplength=760, justify="left")
        self.e_note.pack(anchor="w")
        self.e_holder = ttk.Frame(ed)
        self.e_holder.pack(fill="x", pady=6)
        qframe.pack(fill="both", expand=True, pady=(6, 6))
        split.add(right, weight=3)

    def load_profile(self):
        for k, e in self.p_entries.items():
            e.delete(0, "end")
            e.insert(0, self.bank["profile"].get(k, "") or "")

    def save_profile(self):
        try:
            # saved in Supabase; changes only the profile: answers are never overwritten
            self.bank = bank_mod.set_profile({k: e.get().strip() for k, e in self.p_entries.items()},
                                             self.bank)
        except Exception as e:
            messagebox.showerror("Not saved", f"The profile could not be saved:\n{e}")
            return
        self.log("✓ Profile saved.\n")
        self.refresh_all()
        self.p_saved.config(text=f"✓ Saved  {datetime.now().strftime('%H:%M:%S')}")

    def show_answers_for_selected(self):
        """Jobs tab → Answers tab, listing only questions required by the ticked jobs."""
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Answers for selected",
                                "Tick one or more jobs first (or select a job on the right).")
            return
        # Keep bank in sync so every selected job's required questions are listed
        try:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception as e:
            self.log(f"! Answer bank sync: {e}\n")
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT bank_key, COUNT(DISTINCT job_id) n FROM questions "
            f"WHERE required=1 AND job_id IN ({placeholders}) "
            f"AND bank_key IS NOT NULL AND bank_key != '' "
            f"GROUP BY bank_key", ids).fetchall()
        self.answers_filter_ids = list(ids)
        self.answers_filter_keys = {r["bank_key"]: r["n"] for r in rows}
        titles = []
        for jid in ids[:3]:
            row = self.conn.execute("SELECT title, company FROM jobs WHERE id=?", (jid,)).fetchone()
            if row:
                titles.append(f"{row['company'] or '?'}: {row['title'][:40]}")
        more = f" (+{len(ids) - 3} more)" if len(ids) > 3 else ""
        self.q_filter_lbl.config(
            text=f"Answers for {len(ids)} selected job{'s' if len(ids) != 1 else ''}: "
                 + "; ".join(titles) + more)
        self.q_filter_bar.pack(fill="x", pady=(4, 0), before=self.q_count)
        self.q_only_open.set(False)
        self.q_kind.set("All required")
        self.tabs.select(self.answers_tab)
        self.load_bank_list()
        self.log(f"Answers tab: {len(self.answers_filter_keys)} required question(s) "
                 f"for {len(ids)} selected job(s).\n")

    def clear_answers_job_filter(self):
        """Answers tab: leave the selected-jobs filter and show the full bank again."""
        self.answers_filter_ids = None
        self.answers_filter_keys = None
        self.q_filter_bar.pack_forget()
        self.load_bank_list()

    def import_answers_backup(self):
        """Fill EMPTY answers on this Supabase from a backup of autobid_questions (JSON/CSV).

        Matches by question key or same question text + type. Never overwrites answers
        already set on this board."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Choose autobid_questions backup (JSON or CSV from Supabase)",
            filetypes=[("Backup", "*.json;*.csv"), ("JSON", "*.json"), ("CSV", "*.csv"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
            self.bank, stats = bank_mod.import_matched_answers_from_backup(path, self.bank)
        except Exception as e:
            messagebox.showerror("Import failed", str(e))
            return
        self.load_bank_list()
        self.refresh_all()
        examples = "\n".join(f"  · {t}" for t in stats.get("examples") or [])
        messagebox.showinfo(
            "Import from backup",
            f"Backup answers available: {stats['backup_answered']}\n"
            f"Filled empty matches: {stats['filled']}\n"
            f"Skipped (already answered here): {stats['skipped_has_answer']}\n"
            f"No match in backup: {stats['no_match']}"
            + (f"\n\nExamples:\n{examples}" if examples else ""))
        self.log(f"✓ Imported {stats['filled']} answer(s) from backup "
                 f"({Path(path).name}); skipped {stats['skipped_has_answer']} already filled.\n")

    def load_bank_list(self):
        """Only REQUIRED questions are in the bank. 'You answer' = yes/no, choices, numbers...;
        'AI writes' = text/essay questions the AI answers per job (you may still answer them).
        With a selected-jobs filter, only questions those jobs ask are listed."""
        kind = self.q_kind.get()
        filt = self.answers_filter_keys
        self.qlist.delete(*self.qlist.get_children())
        shown = open_n = 0
        for i, q in enumerate(self.bank["questions"]):
            if filt is not None:
                if q["key"] not in filt:
                    continue
            elif not q.get("asked_in_jobs"):          # not required by any relevant job now
                continue
            is_text = q["type"] in bank_mod.TEXT_TYPES
            if (kind == "You answer" and is_text) or (kind.startswith("AI") and not is_text):
                continue
            empty = bank_mod._is_empty(q["answer"])
            open_n += empty
            if self.q_only_open.get() and not empty:
                continue
            ans = "" if empty else (", ".join(q["answer"]) if isinstance(q["answer"], list) else str(q["answer"]))
            if empty and is_text:
                ans = "(AI writes per job)"
            if filt is not None:
                n_sel = filt.get(q["key"], 0)
                n_jobs = len(self.answers_filter_ids or [])
                jobs_col = f"{n_sel}/{n_jobs} selected"
            else:
                n = q["asked_in_jobs"]
                jobs_col = f"{n} job{'s' if n != 1 else ''}"
            self.qlist.insert("", "end", iid=str(i), tags=("open",) if empty else (),
                              values=(jobs_col, q["type"], q["question"], ans[:80]))
            shown += 1
        if filt is not None:
            n_jobs = len(self.answers_filter_ids or [])
            self.q_count.config(
                text=f"{shown} questions for {n_jobs} selected job{'s' if n_jobs != 1 else ''}  ·  "
                     f"{open_n} still unanswered  ·  click “Show all answers” to clear the filter")
        else:
            self.q_count.config(text=f"{shown} questions shown  ·  {open_n} of them still unanswered  ·  "
                                     f"optional questions are never listed or saved")

    def edit_question(self):
        sel = self.qlist.selection()
        if not sel:
            return
        q = self.bank["questions"][int(sel[0])]
        self.selected_q = q
        self.e_question.config(text=q["question"])
        self.e_note.config(text=q.get("note", ""))
        for w in self.e_holder.winfo_children():
            w.destroy()
        cur = q.get("answer")
        if q["type"] == "Boolean":
            self.e_widget = ttk.Combobox(self.e_holder, values=["Yes", "No"], state="readonly", width=20)
            self.e_widget.set("" if cur is None else ("Yes" if cur in (True, "Yes", "yes") else "No"))
            self.e_widget.pack(anchor="w")
        elif q["type"] == "ValueSelect":
            self.e_widget = ttk.Combobox(self.e_holder, values=q.get("choices", []), state="readonly", width=60)
            self.e_widget.set(cur or "")
            self.e_widget.pack(anchor="w")
        elif q["type"] == "MultiValueSelect":
            ttk.Label(self.e_holder, text="Click to select one or more:",
                      style="Stat.TLabel").pack(anchor="w")
            box = ttk.Frame(self.e_holder)
            box.pack(anchor="w")
            self.e_widget = tk.Listbox(box, selectmode="multiple", height=min(5, len(q.get("choices", []))),
                                       exportselection=False, width=60, activestyle="none",
                                       selectbackground="#1f4fbf")
            lsb = ttk.Scrollbar(box, orient="vertical", command=self.e_widget.yview)
            self.e_widget.configure(yscrollcommand=lsb.set)
            for j, c in enumerate(q.get("choices", [])):
                self.e_widget.insert("end", c)
                if isinstance(cur, list) and c in cur:
                    self.e_widget.selection_set(j)
            self.e_widget.pack(side="left")
            if len(q.get("choices", [])) > 5:
                lsb.pack(side="left", fill="y")
        else:
            self.e_widget = tk.Text(self.e_holder, height=5 if q["type"] == "LongText" else 2,
                                    wrap="word", font=("Segoe UI", 10))
            self.e_widget.insert("1.0", cur or "")
            self.e_widget.pack(fill="x")
            if q["type"] in bank_mod.TEXT_TYPES:
                ttk.Label(self.e_holder, text="Leave empty to let the AI write it for each job.",
                          style="Stat.TLabel").pack(anchor="w")

    def save_answer(self, clear=False):
        q = self.selected_q
        if not q:
            return
        if clear:
            answer = None
        elif q["type"] == "MultiValueSelect":
            answer = [self.e_widget.get(i) for i in self.e_widget.curselection()] or None
        elif isinstance(self.e_widget, tk.Text):
            answer = self.e_widget.get("1.0", "end").strip() or None
        else:
            answer = self.e_widget.get() or None
        # position in the list, to continue with the next question when this one disappears
        # from an "only unanswered" list
        rows = self.qlist.get_children()
        pos = rows.index(self.qlist.selection()[0]) if self.qlist.selection() else 0
        try:
            # saved in Supabase; changes only this question: nothing else is overwritten
            self.bank = bank_mod.set_answer(q["key"], answer, self.bank)
        except Exception as e:
            messagebox.showerror("Not saved", f"The answer could not be saved:\n{e}")
            return
        shown = "(cleared)" if answer is None else (", ".join(answer) if isinstance(answer, list) else answer)
        self.log(f"✓ Saved answer: {q['question'][:70]}  ->  {str(shown)[:60]}\n")
        self.refresh_all()

        idx = next((n for n, b in enumerate(self.bank["questions"]) if b["key"] == q["key"]), None)
        if idx is not None and self.qlist.exists(str(idx)):
            target, note = str(idx), "✓ Saved"
        else:   # answered question is hidden by "only unanswered": go on with the next one
            rows = self.qlist.get_children()
            target = rows[min(pos, len(rows) - 1)] if rows else None
            note = "✓ Saved - next unanswered question"
        if target:
            self.qlist.selection_set(target)
            self.qlist.see(target)
        self.e_saved.config(text=f"{note}  {datetime.now().strftime('%H:%M:%S')}")

    # ------------------------------------------------------------ Run tab

    def _run_tab(self):
        tab = ttk.Frame(self.tabs, padding=14)
        self.tabs.add(tab, text="  Run  ")
        opts = ttk.LabelFrame(tab, text="  Settings for this run  ", padding=10)
        opts.pack(fill="x")
        ttk.Label(opts, text="AI for resumes").grid(row=0, column=0, sticky="w")
        self.r_ai_label = ttk.Label(opts, text="")
        self.r_ai_label.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(opts, text="Change in the AI tab", command=lambda: self.tabs.select(self.ai_tab)).grid(
            row=0, column=2, sticky="w")
        ttk.Label(opts, text="Batch size").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.r_batch = ttk.Spinbox(opts, from_=1, to=20, width=6)
        self.r_batch.set(BATCH_SIZE)
        self.r_batch.grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Label(opts, text="jobs prepared together; you solve their bot checks in a row",
                  style="Stat.TLabel").grid(row=1, column=2, sticky="w", pady=(8, 0))
        self.r_dry = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Dry run (fill forms, do NOT submit)", variable=self.r_dry).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.r_only_sel = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Bid only on the companies selected in the Jobs tab",
                        variable=self.r_only_sel).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))

        auto = ttk.LabelFrame(tab, text="  Auto-collection (US Eastern)  ", padding=10)
        auto.pack(fill="x", pady=(12, 0))
        self.a_on = tk.BooleanVar(value=self.settings["auto_collect"])
        ttk.Checkbutton(auto, text="Automatically run", variable=self.a_on,
                        command=self.save_auto).grid(row=0, column=0, sticky="w")
        self.a_kind = ttk.Combobox(auto, width=22, state="readonly",
                                   values=["Collect today's jobs", "Collect all companies"])
        self.a_kind.set("Collect all companies" if self.settings.get("auto_kind") == "all"
                        else "Collect today's jobs")
        self.a_kind.bind("<<ComboboxSelected>>", lambda e: self.save_auto())
        self.a_kind.grid(row=0, column=1, padx=6)
        ttk.Label(auto, text=f"at {AUTO_SLOTS_LABEL}  (while this app is open)").grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(6, 0))
        ttk.Button(auto, text="⟳ Collect all companies now", style="Accent.TButton",
                   command=self.collect).grid(row=0, column=4, padx=(24, 0))
        self.a_info = ttk.Label(auto, text="", style="Stat.TLabel", justify="left")
        self.a_info.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(auto, text=("Roadmap each run:  1) scrape company boards from companies.csv  →  "
                              "2) update this Jobs list  →  3) sync Supabase.  "
                              "If Apply is running, Collect is queued and starts when Apply finishes."),
                  style="Stat.TLabel", wraplength=900, justify="left").grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

        acts = ttk.Frame(tab, padding=(0, 14, 0, 0))
        acts.pack(fill="x")
        ttk.Button(acts, text="1a. Scrape jobs", command=lambda: self.run_script(
            ["scrape_jobs.py"], "Stage 1: job scraping + relevance")).pack(side="left")
        ttk.Button(acts, text="1b. Scrape details", command=lambda: self.run_script(
            ["scrape_details.py"], "Stage 2: job details scraping")).pack(side="left", padx=(6, 0))
        ttk.Button(acts, text="2. Make missing resumes", command=self.make_all_resumes).pack(side="left", padx=6)
        self.b_start = ttk.Button(acts, text="3. ▶ Start bidding", style="Accent.TButton", command=self.start_bid)
        self.b_start.pack(side="left")
        self.b_stop = ttk.Button(acts, text="■ Stop", command=self.stop, state="disabled")
        self.b_stop.pack(side="left", padx=6)

        self.r_summary = ttk.Label(tab, text="", justify="left", padding=(0, 16, 0, 0))
        self.r_summary.pack(anchor="w")
        ttk.Label(tab, style="Stat.TLabel", justify="left", padding=(0, 12, 0, 0), text=(
            "Batch mode: the bot fills each job's required fields, uploads its tailored resume and "
            "clicks Submit.\nIf a bot check appears, the tab stays open and you get one notification "
            "for the batch - solve them in a row,\neach tab submits and closes by itself. "
            "Ashby allows max 3 applications in 60 days; the bot keeps to that.")).pack(anchor="w")

    def update_summary(self):
        rows = dict(self.conn.execute("SELECT status, COUNT(*) FROM jobs WHERE is_active=1 AND relevant=1 GROUP BY status").fetchall())
        ready = waiting = blocked = 0
        for j in self.conn.execute("SELECT id FROM jobs WHERE is_active=1 AND relevant=1 AND status IN ('ready','needs_you')"):
            missing = bank_mod.blocking(self.resolved.get(j["id"], []))
            if not missing:
                ready += 1
            elif all(bank_mod.ai_writable(i) for i in missing):
                waiting += 1
            else:
                blocked += 1
        applied = sum(rows.get(s, 0) for s in db.APPLIED_STATUSES)
        resumes = self.conn.execute("SELECT COUNT(*) FROM jobs WHERE resume_path IS NOT NULL AND resume_path != ''").fetchone()[0]
        total = sum(rows.values())
        self.stats.config(text=f"{total} jobs   ·   {applied} applied   ·   "
                               f"{rows.get('needs_you', 0)} need you   ·   {resumes} resumes")
        self.r_summary.config(text=(
            f"Ready to apply now:  {ready}\n"
            f"AI will write essays for:  {waiting}   (text/essay questions left empty in Answers)\n"
            f"Missing profile / yes-no / choice answers:  {blocked}   → fill the Answers tab\n"
            f"Applied: {applied}   ·   Waiting for your bot check: {rows.get('needs_you', 0)}   ·   "
            f"Failed: {rows.get('failed', 0)}"))

    # ------------------------------------------------------------ actions

    def show_todays_jobs(self):
        """One click: relevant + Posted=today so today's haul is easy to see."""
        self.f_relevant.set("relevant")
        self.f_status.set("all")
        self.f_answers.set("any")
        self.f_resume.set("any")
        self.f_posted.set("today")
        self.load_jobs()
        self.tabs.select(0)

    def collect(self, auto=False):
        """Stage 1 + stage 2 for every company in companies.csv (queued if Apply is running)."""
        return self._start_or_queue_collect("all", auto=auto)

    def collect_today(self, auto=False):
        """Jobs new on the boards since the last collection; then shows the ones posted today.
        Queued if Apply / another task is running — starts as soon as that finishes."""
        return self._start_or_queue_collect("today", auto=auto)

    def _start_or_queue_collect(self, kind, auto=False):
        """Start a collection now, or queue it if the bot is busy (e.g. Apply)."""
        busy = self.proc and self.proc.poll() is None
        if busy:
            # Prefer an explicit "all" over a queued "today" if both were requested
            if self._pending_collect != "all":
                self._pending_collect = kind
            label = "today's jobs" if kind == "today" else "all companies"
            if not auto or not getattr(self, "_auto_busy_logged", False):
                self.log(f"(collection queued: {label} — starts when the current task finishes; "
                         f"1 companies.csv → 2 Jobs list → 3 Supabase)\n")
            if auto:
                self._auto_busy_logged = True
            self._show_queue_hint()
            return True
        return self._run_collect(kind, auto=auto)

    def _run_collect(self, kind, auto=False):
        self._pending_collect = None
        self._auto_busy_logged = False
        if kind == "today":
            ok = self.run_script(["collect.py", "--today"],
                                 "Collecting today's jobs" + (" (auto)" if auto else ""), quiet=auto)
        else:
            ok = self.run_script(["collect.py"],
                                 "Collecting all companies" + (" (auto)" if auto else ""), quiet=auto)
        if ok:
            self._mark_auto_slot_covered()
            self.log("Collection roadmap: 1) scrape companies.csv boards → "
                     "2) update Jobs list → 3) sync Supabase\n")
        return ok

    def _show_queue_hint(self):
        kind = self._pending_collect
        if not kind:
            return
        label = "Collect today's jobs" if kind == "today" else "Collect all companies"
        try:
            self.run_label.config(text=f"{self.run_label.cget('text').split(' · queued')[0]} · queued: {label}")
        except Exception:
            pass

    def _mark_auto_slot_covered(self):
        """After any collection starts, mark the current Eastern slot so 'next' stays fixed."""
        slot = latest_passed_slot()
        self.settings["auto_last_slot"] = slot.isoformat()
        save_settings(self.settings)
        self._auto_info()

    def fetch_missing_details(self, auto=False):
        """Stage 2 only: download description + questions for relevant jobs still on
        'no details yet' / status=new (e.g. reopened from not-relevant). Makes them biddable."""
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 AND details_at IS NULL"
        ).fetchone()["c"]
        if not n:
            if not auto:
                messagebox.showinfo("Fetch missing details",
                                    "Every relevant job already has details. Nothing to fetch.")
            return False
        if not auto and not messagebox.askyesno(
                "Fetch missing details",
                f"{n} relevant job(s) still show “no details yet” (often reopened from "
                f"not-relevant). Download their questions now so you can Apply?"):
            return False
        return self.run_script(["scrape_details.py"],
                               f"Fetching details for {n} job(s)" + (" (auto)" if auto else ""),
                               quiet=auto)

    def sync_from_db(self):
        """Download: jobs changed in Supabase (e.g. on another computer) come to this board."""
        return self.run_script(["sync_db.py", "--pull"], "Sync from DB (Supabase → this computer)")

    def sync_to_db(self):
        """Upload: jobs changed on this computer go to Supabase."""
        return self.run_script(["sync_db.py", "--push"], "Sync to DB (this computer → Supabase)")

    def set_status_selected(self):
        status = self.d_status.get()
        ids = self.selected_ids()
        if not ids or not status:
            return
        if len(ids) > 1 and not messagebox.askyesno(
                "Set status", f"Set the status of {len(ids)} ticked jobs to \"{status}\"?"):
            return
        for jid in ids:
            db.set_status(self.conn, jid, status)
        self.log(f"✓ Status \"{status}\": {self._selection_label(ids)}\n")
        self.refresh_all()

        def push():                                    # to Supabase, without freezing the window
            try:
                import sync_db
                conn = db.connect()
                n = sync_db.push_jobs(conn, ids)
                conn.close()
                self.ui_q.put(lambda: self.log(f"  saved to Supabase ({n} job{'s' if n != 1 else ''})\n"))
            except Exception as e:
                msg = str(e)[:200]
                self.ui_q.put(lambda: self.log(f"! Status not saved to Supabase yet ({msg}) - "
                                               f"click 'Sync to DB' later\n"))
        threading.Thread(target=push, daemon=True).start()

    # ---------------- auto-collection

    def save_auto(self):
        turned_on = bool(self.a_on.get()) and not self.settings["auto_collect"]
        self.settings["auto_collect"] = bool(self.a_on.get())
        self.settings["auto_kind"] = "all" if self.a_kind.get() == "Collect all companies" else "today"
        if turned_on:
            # Just enabled: run the current Eastern slot once (do not wait for the next clock time).
            self.settings["auto_last_slot"] = None
        save_settings(self.settings)
        self._auto_info()

    def _due_slot(self):
        """Eastern slot that should run now, or None if already covered / not yet time."""
        last = parse_slot(self.settings.get("auto_last_slot"))
        passed = latest_passed_slot()
        if last is None or passed > last:
            return passed
        return None

    def _next_due(self):
        """Next fixed Eastern clock time (stable — does not move when a run finishes)."""
        last = parse_slot(self.settings.get("auto_last_slot"))
        after = last if last and last > _now_et() else _now_et()
        # If a slot is currently due, "next" for display is that slot; else the upcoming one.
        due = self._due_slot()
        return due if due else next_auto_slot(after)

    def _auto_info(self):
        last = self.settings.get("last_collect")
        last_txt = datetime.fromisoformat(last).strftime("%b %d %H:%M") if last else "never"
        what = "today's jobs" if self.settings.get("auto_kind") != "all" else "all companies"
        if self.settings["auto_collect"]:
            nxt = self._next_due()
            nxt_et = nxt.astimezone(ET)
            local = nxt.astimezone()  # machine local, for the header clock people glance at
            due_now = self._due_slot() is not None
            nxt_txt = ("due now" if due_now else
                       f"{nxt_et.strftime('%b %d %H:%M')} ET"
                       + ("" if local.tzinfo == ET else f" ({local.strftime('%H:%M')} local)"))
            queued = f"   ·   queued after current task" if self._pending_collect else ""
            txt = (f"Last collection: {last_txt}   ·   auto: {what} at fixed ET times   ·   "
                   f"next: {nxt_txt}{queued}\nSlots: {AUTO_SLOTS_LABEL}")
            self.auto_hdr.config(
                text=f"auto-collect ({what}) · next {nxt_et.strftime('%H:%M')} ET"
                     + (" · due" if due_now else "")
                     + (" · queued" if self._pending_collect else ""))
        else:
            txt = f"Last collection: {last_txt}   ·   auto-collection is off"
            self.auto_hdr.config(text="")
        self.a_info.config(text=txt)

    def _auto_tick(self, reschedule=True):
        """Start a collection when a fixed Eastern slot is due.
        Called every minute, and also right after another task finishes if overdue.
        If Apply is running, Collect is queued and starts when Apply finishes."""
        try:
            try:
                self.settings = load_settings()
            except Exception:
                pass
            due = self._due_slot() if self.settings.get("auto_collect") else None
            if due is not None:
                kind = "all" if self.settings.get("auto_kind") == "all" else "today"
                if self._start_or_queue_collect(kind, auto=True):
                    if not (self.proc and self.proc.poll() is None and self._pending_collect):
                        self.log(f"(auto-collection @ {due.strftime('%H:%M')} ET: {kind}; "
                                 f"1 companies.csv → 2 Jobs list → 3 Supabase; "
                                 f"next slot {next_auto_slot(due).strftime('%H:%M')} ET)\n")
            self._auto_info()
        finally:
            if reschedule:
                self.after(60_000, self._auto_tick)

    def _selection_label(self, ids):
        if len(ids) == 1:
            return self.jobs.set(ids[0], "title")
        return f"{len(ids)} selected jobs"

    def open_bot_browser(self):
        """Open AutoBid Chrome (.browser-profile) so you can sign in to AI sites before the bot runs."""
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning(
                "Busy",
                "Something is already running. Stop it first — the bot Chrome profile "
                "can only be open in one place at a time.")
            return
        self.run_script(
            ["open_browser_login.py"],
            "Bot Chrome — sign in to ChatGPT / Claude / DeepSeek")

    def _flush_ai_settings(self):
        """Write the AI tab (including an unsaved Platform change) to disk before any AI task.
        Prevents bid.py from still using claude_api after you switched to ChatGPT web."""
        if getattr(self, "ai_widgets", None):
            self.save_ai()

    def make_resume_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        if len(ids) > 3 and not messagebox.askyesno(
                "Make resumes", f"Write {len(ids)} tailored resumes with the AI?\n"
                                f"(about 1-2 minutes each, one after another)"):
            return
        self._flush_ai_settings()
        self.run_script(["make_resume.py", "--jobs-file", self._write_selection(ids)],
                        f"Resume for {self._selection_label(ids)}")

    def make_all_resumes(self):
        self._flush_ai_settings()
        self.run_script(["make_resume.py", "--all"], "Making missing resumes")

    def _selected_missing_details(self, ids):
        """Job ids among `ids` that are still “no details yet” (cannot Apply)."""
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT id, title, company FROM jobs WHERE id IN ({placeholders}) "
            f"AND (details_at IS NULL OR details_at='')", ids).fetchall()
        return list(rows)

    def dry_run_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        missing = self._selected_missing_details(ids)
        if missing:
            if messagebox.askyesno(
                    "No details yet",
                    f"{len(missing)} of {len(ids)} selected job(s) still have no questions "
                    f"(Answers: “no details yet”). Fetch details first?"):
                self.fetch_missing_details()
            return
        self._flush_ai_settings()
        self.run_script(["bid.py", "--dry-run", "--batch", self.r_batch.get(),
                         "--jobs-file", self._write_selection(ids)],
                        f"Dry run: {self._selection_label(ids)}")

    def apply_selected(self):
        ids = self.selected_ids()
        if not ids:
            return
        missing = self._selected_missing_details(ids)
        if missing:
            if messagebox.askyesno(
                    "No details yet",
                    f"{len(missing)} of {len(ids)} selected job(s) still have no questions "
                    f"(often reopened from not-relevant). You cannot Apply until details are "
                    f"fetched.\n\nFetch missing details now?"):
                self.fetch_missing_details()
            return
        titles = [f"• {self.jobs.set(i, 'title')}  ({self.jobs.set(i, 'company')})" for i in ids[:12]]
        more = f"\n... and {len(ids) - 12} more" if len(ids) > 12 else ""
        if not messagebox.askyesno("Apply", (
                f"Really apply to {len(ids)} job(s)? This SUBMITS the applications.\n\n"
                + "\n".join(titles) + more + "\n\n"
                f"Batch mode, {self.r_batch.get()} at a time. Empty text/essay questions are "
                f"written by the AI per job. Jobs missing profile / yes-no / choice answers are "
                f"skipped and listed in the log. Company limits (Ashby: 3 in 60 days) apply.")):
            return
        self._flush_ai_settings()
        self.run_script(["bid.py", "--batch", self.r_batch.get(), "--jobs-file", self._write_selection(ids)],
                        f"Applying: {self._selection_label(ids)}")

    def start_bid(self):
        args = ["bid.py", "--batch", self.r_batch.get()]
        if self.r_only_sel.get():
            if not self.settings["companies"]:
                messagebox.showinfo("Companies", "No companies are selected in the Jobs tab - bidding on all.")
            else:
                args.append("--selected-companies")
        if self.r_dry.get():
            args.append("--dry-run")
        elif not messagebox.askyesno("Start bidding", "Dry run is off: applications will be SUBMITTED. Continue?"):
            return
        self._flush_ai_settings()
        self.run_script(args, "Bidding" + (" (dry run)" if self.r_dry.get() else ""))

    def stop(self):
        """Stop now: end the task AND everything it started (browser, AI call, uploads)."""
        if not (self.proc and self.proc.poll() is None):
            return
        PAUSE_FLAG.unlink(missing_ok=True)   # unblock any wait_while_paused before kill
        pid = self.proc.pid
        try:
            if sys.platform == "win32":
                # /T = the whole process tree (Chrome, Playwright driver, claude.exe ...)
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20)
            else:
                self.proc.kill()
        except Exception:
            self.proc.kill()
        self._set_pause_button(paused=False)
        self.log("\n■ Stopped now (the task, its browser and its AI call were ended). "
                 "A job in progress keeps its old status.\n")

    def stop_after(self):
        """Stop after this job: the script finishes the current job cleanly, then stops."""
        if not (self.proc and self.proc.poll() is None):
            return
        # If paused, leave pause on - stop.flag is checked after wait_while_paused returns
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text("stop", encoding="utf-8")
        self.b_stop_after.config(text="stopping after this job …", state="disabled")
        self.log("\nWill stop after the current job (press 'Stop now' to end at once).\n")

    def toggle_pause(self):
        """Pause: finish the current job/step, then wait. Continue: resume the queue."""
        if not (self.proc and self.proc.poll() is None):
            return
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink(missing_ok=True)
            self._set_pause_button(paused=False)
            self.run_label.config(text=f"●  Running: {self.proc_label}")
            self.run_bar.config(bg="#fff4e5")
            self.run_label.config(bg="#fff4e5", fg="#7a4a00")
            self.log("\n▶ Continued.\n")
        else:
            PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            PAUSE_FLAG.write_text("pause", encoding="utf-8")
            self._set_pause_button(paused=True)
            self.run_label.config(text=f"⏸  Paused: {self.proc_label}  (finishes current step, then waits)")
            self.log("\n⏸ Paused - finishes the current step, then waits. "
                     "Press ▶ Continue to resume, or Stop to end.\n")

    def _set_pause_button(self, paused):
        if paused:
            self.b_pause.config(text="▶  Continue", bg="#1a7f37", fg="white",
                                activebackground="#14622a", activeforeground="white")
        else:
            self.b_pause.config(text="⏸  Pause", bg="#ffffff", fg="#7a4a00",
                                activebackground="#f5f5f5", activeforeground="#7a4a00")

    def _show_run_bar(self, label):
        self.run_stats = {"ok": 0, "fail": 0, "need": 0}
        self.run_label.config(text=f"●  Running: {label}")
        self._update_run_stats()
        self.b_stop_after.config(text="Stop after this job", state="normal")
        self._set_pause_button(paused=False)
        self.b_pause.config(state="normal")
        self.run_bar.pack(fill="x", padx=10, pady=(0, 6), after=self._header_frame)

    def _hide_run_bar(self):
        PAUSE_FLAG.unlink(missing_ok=True)
        self._set_pause_button(paused=False)
        self.run_bar.pack_forget()

    def _update_run_stats(self):
        """Refresh the live counters on the orange run bar (this run only)."""
        s = self.run_stats
        bidding = self.proc_label.startswith(("Bidding", "Applying", "Dry run"))
        if bidding:
            self.run_ok.config(text=f"✓ {s['ok']} success")
            self.run_fail.config(text=f"✗ {s['fail']} failed")
            self.run_need.config(text=f"⚠ {s['need']} need you")
        else:
            self.run_ok.config(text="")
            self.run_fail.config(text="")
            self.run_need.config(text="")

    def _track_run_line(self, line):
        """Parse one bid.py log line and bump success / failed / need-you for this run."""
        if not self.proc_label.startswith(("Bidding", "Applying", "Dry run")):
            return
        t = line.strip()
        # success
        if t == "APPLIED" or t.startswith("APPLIED:") or t.startswith("APPLIED (by you)"):
            self.run_stats["ok"] += 1
        # need you (bot check / unclear / spam retry left open)
        elif ("bot check -> waiting for you" in t
              or t.startswith("UNCLEAR")
              or t.startswith("NOT SUBMITTED")
              or "needs_you" in t.lower() and "stays" in t):
            self.run_stats["need"] += 1
        # failed (form/page error) — not company-limit (job goes back to ready)
        elif (t.startswith("FAILED") or "FAILED -" in t
              or t.startswith("failed:") or t.startswith("NOT applied")):
            self.run_stats["fail"] += 1
        else:
            return
        self._update_run_stats()
        # keep the header totals in sync while applying
        try:
            self.update_summary()
        except Exception:
            pass

    # ------------------------------------------------------------ child process + log

    # ------------------------------------------------------------ AI tab

    def _ai_tab(self):
        tab = ttk.Frame(self.tabs, padding=12)
        self.ai_tab = tab
        self.tabs.add(tab, text="  AI  ")
        self.ai_conf = ai_settings.load()
        self.ai_widgets = {}

        top = ttk.Frame(tab)
        top.pack(fill="x")
        for col, (task, title, hint) in enumerate([
                ("resume", "  Resume writing  ", "Writes the tailored resume JSON for each job."),
                ("relevance", "  Job sorting  ", "Decides unclear job titles in stage 1 (many short calls: "
                                                 "a fast model like haiku is enough).")]):
            box = ttk.LabelFrame(top, text=title, padding=10)
            box.grid(row=0, column=col, sticky="nsew", padx=(0, 10) if col == 0 else 0)
            top.columnconfigure(col, weight=1)
            ttk.Label(box, text=hint, style="Stat.TLabel", wraplength=480).grid(
                row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
            ttk.Label(box, text="Platform").grid(row=1, column=0, sticky="w")
            plat = ttk.Combobox(box, state="readonly", width=48,
                                values=[f"{k} - {v}" for k, v in ai_settings.PROVIDERS.items()])
            plat.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=2)
            model_lbl = ttk.Label(box, text="Model")
            model_lbl.grid(row=2, column=0, sticky="w")
            model = ttk.Combobox(box, width=26)   # editable: any model name can be typed
            model.grid(row=2, column=1, sticky="w", padx=6, pady=2)
            test_btn = ttk.Button(box, text="Test", command=lambda t=task: self.test_ai(t))
            test_btn.grid(row=2, column=2, sticky="w")
            test_lbl = ttk.Label(box, text="", style="Stat.TLabel", wraplength=480)
            model_hint = ttk.Label(box, text="Empty model = the platform's default. Type any model name, e.g. "
                                             "claude-opus-5-5.", style="Stat.TLabel")
            model_hint.grid(row=3, column=0, columnspan=3, sticky="w")
            web_hint = ttk.Label(box, text="Web chat pages use the model selected on the site "
                                           "(no model box here).", style="Stat.TLabel")
            test_lbl.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

            provider = self.ai_conf[task]["provider"]
            plat.set(f"{provider} - {ai_settings.PROVIDERS.get(provider, '')}")
            model["values"] = ai_settings.MODELS.get(provider, [""])
            model.set(self.ai_conf[task]["model"])
            self.ai_widgets[task] = (plat, model, test_lbl, model_lbl, test_btn, model_hint, web_hint)
            plat.bind("<<ComboboxSelected>>", lambda e, t=task: self._on_ai_platform(t))
            self._ai_platform_ui(task)

        kbox = ttk.LabelFrame(tab, text="  API keys (for claude_api / openai_api)  ", padding=10)
        kbox.pack(fill="x", pady=(10, 0))
        ttk.Label(kbox, text="Saved in Supabase as Resume Builder's keys, so the web app, the Chrome "
                             "extension and every computer use the same ones. Paid per use; tokens and "
                             "cost of each call are saved in the ai_usage table.",
                  style="Stat.TLabel", wraplength=900).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        self.key_entries = {}
        for r, (prov, label) in enumerate([("claude_api", "Claude (Anthropic) key"),
                                           ("openai_api", "OpenAI key")], 1):
            ttk.Label(kbox, text=label).grid(row=r, column=0, sticky="w")
            e = ttk.Entry(kbox, width=70, show="•")
            e.grid(row=r, column=1, sticky="w", padx=6, pady=2)
            self.key_entries[prov] = e
        self.k_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(kbox, text="Show", variable=self.k_show, command=lambda: [
            e.config(show="" if self.k_show.get() else "•") for e in self.key_entries.values()]).grid(
            row=1, column=2, sticky="w")
        ttk.Button(kbox, text="Save keys", command=self.save_keys).grid(row=2, column=2, sticky="w")
        self.k_status = ttk.Label(kbox, text="loading saved keys ...", style="Stat.TLabel")
        self.k_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.after(200, self.load_keys)

        browser_bar = ttk.Frame(tab)
        browser_bar.pack(fill="x", pady=(10, 0))
        ttk.Button(browser_bar, text="Open bot browser", style="Accent.TButton",
                   command=self.open_bot_browser).pack(side="left")
        ttk.Label(browser_bar, style="Stat.TLabel", wraplength=780, text=(
            "  Opens the bot's own Chrome (.browser-profile) with ChatGPT, Claude.ai and DeepSeek. "
            "Sign in there before Apply / Make resume — your normal Chrome login does not count. "
            "Close that window before starting the bot.")).pack(side="left", padx=(8, 0))
        ttk.Button(tab, text="Save AI settings", style="Accent.TButton", command=self.save_ai).pack(
            anchor="e", pady=(8, 4))

        pbox = ttk.LabelFrame(tab, text="  Resume JSON prompt  ", padding=10)
        pbox.pack(fill="both", expand=True)
        src = ttk.Frame(pbox)
        src.pack(fill="x")
        self.p_source = tk.StringVar(value=self.ai_conf["prompt_source"])
        ttk.Radiobutton(src, text="Resume Builder prompt (shared with the Chrome extension and web app)",
                        value="resume_builder", variable=self.p_source,
                        command=self.prompt_source_changed).pack(side="left")
        ttk.Radiobutton(src, text="Local prompt (AutoBid only)", value="local", variable=self.p_source,
                        command=self.prompt_source_changed).pack(side="left", padx=16)
        self.p_text = ScrolledText(pbox, wrap="word", font=("Consolas", 9), height=12, undo=True)
        self.p_text.pack(fill="both", expand=True, pady=6)
        self.p_text.bind("<<Modified>>", self._prompt_modified)
        pb = ttk.Frame(pbox)
        pb.pack(fill="x")
        self.p_info = ttk.Label(pb, text="", style="Stat.TLabel")
        self.p_info.pack(side="left")
        ttk.Button(pb, text="Save prompt", style="Accent.TButton", command=self.save_prompt).pack(side="right")
        ttk.Button(pb, text="Built-in default", command=lambda: self.load_prompt("default")).pack(side="right", padx=6)
        ttk.Button(pb, text="Reload", command=lambda: self.load_prompt()).pack(side="right")
        ttk.Label(pbox, style="Stat.TLabel", text=(
            "The JSON output rules (schema, one entry per job, fixed second company name, contact details) "
            "are added after this prompt automatically - write only what the resume should say.")).pack(anchor="w")
        self._rb_prompt = None      # Resume Builder data, loaded in the background
        self.load_prompt()
        self._ai_label()

    def _ai_label(self):
        p, m = ai_settings.get("resume")
        self.r_ai_label.config(text=f"{p}" + (f"  ·  {m}" if m else "  ·  default model"))

    def _ai_choice(self, task):
        plat, model = self.ai_widgets[task][0], self.ai_widgets[task][1]
        provider = plat.get().split(" - ")[0]
        # web pages have no model (the chat site picks it)
        if provider in ai_settings.WEB_PROVIDERS:
            return provider, ""
        return provider, model.get().strip()

    def _ai_platform_ui(self, task):
        """Show/hide the Model row: web chat pages have no model setting."""
        plat, model, _test_lbl, model_lbl, test_btn, model_hint, web_hint = self.ai_widgets[task]
        provider = plat.get().split(" - ")[0]
        if provider in ai_settings.WEB_PROVIDERS:
            model.set("")
            model_lbl.grid_remove()
            model.grid_remove()
            test_btn.grid_remove()
            model_hint.grid_remove()
            web_hint.grid(row=3, column=0, columnspan=3, sticky="w")
        else:
            web_hint.grid_remove()
            model_lbl.grid()
            model.grid()
            test_btn.grid()
            model_hint.grid()
            model["values"] = ai_settings.MODELS.get(provider, [""])
            if model.get() not in (model["values"] or [""]) and model.get():
                pass  # keep a typed custom model name
            elif not model.get() and ai_settings.MODELS.get(provider):
                model.set(ai_settings.MODELS[provider][0])

    def _on_ai_platform(self, task):
        self._ai_platform_ui(task)
        self.save_ai()   # so bidding never keeps using the previous API after you switch

    def save_ai(self):
        for task in ("resume", "relevance"):
            provider, model = self._ai_choice(task)
            self.ai_conf[task] = {"provider": provider, "model": model}
        self.ai_conf["prompt_source"] = self.p_source.get()
        ai_settings.save(self.ai_conf)
        self._ai_label()
        self.log("AI settings saved: resume = %s %s | job sorting = %s %s\n" % (
            self.ai_conf["resume"]["provider"], self.ai_conf["resume"]["model"] or "(default)",
            self.ai_conf["relevance"]["provider"], self.ai_conf["relevance"]["model"] or "(default)"))

    def _key_status(self, keys):
        parts = [f"{'Claude' if p == 'claude_api' else 'OpenAI'}: " +
                 (f"saved (…{v[-4:]})" if v else "not set") for p, v in keys.items()]
        return "   ·   ".join(parts)

    def load_keys(self):
        """Saved API keys from Supabase, in the background (the window opens at once)."""
        def work():
            try:
                keys = ai_settings.load_api_keys()

                def show():
                    for p, e in self.key_entries.items():
                        e.delete(0, "end")
                        e.insert(0, keys.get(p, ""))
                    self.k_status.config(text=self._key_status(keys))
            except Exception as ex:
                msg = str(ex)[:160]

                def show():
                    self.k_status.config(text=f"Could not load the keys from Supabase: {msg}")
            self.ui_q.put(show)
        threading.Thread(target=work, daemon=True).start()

    def save_keys(self):
        keys = {p: e.get().strip() for p, e in self.key_entries.items()}
        try:
            ai_settings.save_api_keys(keys)
        except Exception as e:
            messagebox.showerror("Not saved", f"The API keys could not be saved:\n{e}")
            return
        self.k_status.config(text="✓ Saved   ·   " + self._key_status(keys))
        self.log("✓ API keys saved in Supabase (shared with Resume Builder).\n")

    def test_ai(self, task):
        """Ask the chosen platform/model a one-line question in the background."""
        provider, model = self._ai_choice(task)
        lbl = self.ai_widgets[task][2]
        if provider not in ai_settings.NO_BROWSER:
            lbl.config(text="Web chat pages are tested by running the bot (they open in its Chrome window).")
            return
        lbl.config(text=f"Testing {provider} {model or '(default model)'} ...")

        def work():
            import time as _t
            from web_ai import WebAI
            started = _t.time()
            try:
                problem = ai_settings.api_key_problem(provider)
                if problem:
                    raise RuntimeError(problem)
                ai = WebAI(provider, model=model, task="test")
                reply = ai.ask("Reply with one short line: OK and the name of the model you are.").strip()
                used = f"  [{ai.last_model}]" if ai.last_model else ""
                msg = f"✓ {reply[:120]}{used}   ({_t.time() - started:.0f}s)"
            except Exception as e:
                msg = f"✗ {str(e)[:200]}"
            self.ui_q.put(lambda: lbl.config(text=msg))
        threading.Thread(target=work, daemon=True).start()

    # ---------------- resume prompt

    def _prompt_modified(self, _e=None):
        if self.p_text.edit_modified():
            self.p_info.config(text=f"{len(self.p_text.get('1.0', 'end').strip()):,} characters · not saved")

    def _set_prompt_text(self, text, note):
        self.p_text.delete("1.0", "end")
        self.p_text.insert("1.0", text)
        self.p_text.edit_modified(False)
        self.p_info.config(text=f"{len(text):,} characters · {note}")

    def load_prompt(self, which=None):
        """which: None = the selected source, 'default' = the app's built-in prompt."""
        source = self.p_source.get()
        if source == "local" and which is None and ai_settings.local_prompt():
            self._set_prompt_text(ai_settings.local_prompt(), "local prompt (resume_prompt.txt)")
            return
        if self._rb_prompt is not None:
            if which == "default":
                self._set_prompt_text(self._rb_prompt["defaultPrompt"], "built-in default - Save to use it")
            else:
                note = "from Resume Builder" if source == "resume_builder" else \
                    "copied from Resume Builder - Save to keep it as the local prompt"
                self._set_prompt_text(self._rb_prompt["instructions"], note)
            return
        self.p_info.config(text="loading the prompt from Resume Builder ...")

        def work():
            from resume import _node
            try:
                data = _node("settings")
            except Exception as e:
                err = f"could not load: {str(e)[:150]}"
                self.ui_q.put(lambda: self.p_info.config(text=err))
                return
            self._rb_prompt = data
            self.ui_q.put(lambda: self.load_prompt(which))
        threading.Thread(target=work, daemon=True).start()

    def prompt_source_changed(self):
        self.ai_conf["prompt_source"] = self.p_source.get()
        ai_settings.save(self.ai_conf)
        self.load_prompt()

    def save_prompt(self):
        text = self.p_text.get("1.0", "end").strip()
        if len(text) < 50:
            messagebox.showwarning("Prompt", "The prompt is (almost) empty.")
            return
        if self.p_source.get() == "local":
            ai_settings.save_local_prompt(text)
            self.p_text.edit_modified(False)
            self.p_info.config(text=f"{len(text):,} characters · saved as the local prompt")
            self.log("Resume prompt saved (local, AutoBid only).\n")
            return
        if not messagebox.askyesno("Save to Resume Builder", (
                "This changes the resume prompt in Resume Builder (Supabase), so the Chrome extension "
                "and the web app will use it too.\n\nSave?")):
            return
        self.p_info.config(text="saving to Resume Builder ...")

        def work():
            from resume import save_prompt_to_resume_builder
            try:
                save_prompt_to_resume_builder(text)
                if self._rb_prompt:
                    self._rb_prompt["instructions"] = text
                msg = f"{len(text):,} characters · saved to Resume Builder"
                self.ui_q.put(lambda: (self.p_text.edit_modified(False), self.log("Resume prompt saved to Resume Builder.\n")))
            except Exception as e:
                msg = f"save failed: {str(e)[:150]}"
            self.ui_q.put(lambda: self.p_info.config(text=msg))
        threading.Thread(target=work, daemon=True).start()

    def _log_panel(self):
        frame = ttk.Frame(self)
        head = ttk.Frame(frame)
        head.pack(fill="x")
        ttk.Label(head, text="Log", style="H.TLabel").pack(side="left")
        self.busy = ttk.Label(head, text="", style="Stat.TLabel")
        self.busy.pack(side="left", padx=10)
        ttk.Button(head, text="Clear", command=lambda: self.logbox.delete("1.0", "end")).pack(side="right")
        self.logbox = ScrolledText(frame, height=10, font=("Consolas", 9), bg="#1e1e1e", fg="#dcdcdc",
                                   insertbackground="#dcdcdc", relief="flat")
        self.logbox.pack(fill="both", expand=True)
        self.logbox.tag_configure("warn", foreground="#ffb74d")
        self.logbox.tag_configure("ok", foreground="#81c784")
        return frame

    def log(self, text):
        tag = "warn" if (">>>" in text or "!" in text[:6]) else ("ok" if "APPLIED" in text or "done in" in text else "")
        if "FAILED" in text:
            tag = "warn"
        self.logbox.insert("end", text, tag)
        self.logbox.see("end")
        self._track_run_line(text)

    def run_script(self, args, label, quiet=False):
        """Run a bot script as a child process. Returns False if something is already running."""
        if self.proc and self.proc.poll() is None:
            if not quiet:
                messagebox.showwarning("Busy", "Something is already running. Stop it first.")
            return False
        self.proc_label = label
        STOP_FLAG.unlink(missing_ok=True)     # an old "stop after this job" must not stop the new task
        PAUSE_FLAG.unlink(missing_ok=True)    # an old Pause must not block the new task
        self.log(f"\n━━ {label} ━━\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [PY, "-u", *args], cwd=BASE_DIR, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.busy.config(text=f"● running: {label}")
        self._show_run_bar(label)
        self.b_start.config(state="disabled")
        self.b_stop.config(state="normal")
        proc = self.proc

        def pump():
            for line in proc.stdout:
                self.out_q.put(line)
            proc.wait()
            self.out_q.put(None)
        threading.Thread(target=pump, daemon=True).start()
        return True

    def _drain_output(self):
        while not self.ui_q.empty():          # results of background threads (AI test, prompt)
            try:
                self.ui_q.get_nowait()()
            except Exception as e:
                self.log(f"! {e}\n")
        try:
            while True:
                line = self.out_q.get_nowait()
                if line is None:
                    finished_label = self.proc_label
                    self.busy.config(text="")
                    self._hide_run_bar()
                    STOP_FLAG.unlink(missing_ok=True)
                    self.b_start.config(state="normal")
                    self.b_stop.config(state="disabled")
                    self.log("━━ finished ━━\n")
                    if finished_label.startswith("Collecting") and self.proc and self.proc.returncode == 0:
                        self.settings["last_collect"] = datetime.now().isoformat(timespec="seconds")
                        save_settings(self.settings)
                        self._auto_info()
                    if finished_label.startswith("Collecting"):
                        self._sort_unsorted()
                        # show the jobs this scrape found (Posted = today uses first_seen_at too)
                        self.f_relevant.set("relevant")
                        self.f_status.set("all")
                        self.f_answers.set("any")
                        self.f_posted.set("today")
                    if finished_label.startswith("Fetching details"):
                        # Reopened / previously skipped jobs now have questions → show ready ones
                        self.f_relevant.set("relevant")
                        self.f_status.set("ready")
                        self.f_answers.set("any")
                    try:
                        self.bank = bank_mod.load_bank()
                        bank_mod.sync_bank(self.conn, self.bank)
                        bank_mod.save_bank(self.bank)
                    except Exception as e:
                        self.log(f"!! Answer bank not refreshed from Supabase: {e}\n")
                    self.refresh_all()
                    if finished_label.startswith("Collecting"):
                        self._log_collect_result()
                        # Safety net: even if collect.py was killed mid-run ("Stop now"),
                        # push whatever is already in the local DB to Supabase, then refresh again.
                        self._sync_after_collect()
                    elif finished_label.startswith("Fetching details"):
                        missing = self.conn.execute(
                            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 "
                            "AND details_at IS NULL").fetchone()["c"]
                        ready = self.conn.execute(
                            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 "
                            "AND status='ready'").fetchone()["c"]
                        self.log(f"Details fetch done → {ready} ready to apply"
                                 + (f", {missing} still missing (retry Fetch missing details)\n"
                                    if missing else ".\n"))
                        self._sync_after_collect()
                    # Apply/resume/etc. just freed the bot: run a queued Collect first, else
                    # start auto-collect if an Eastern slot is due (don't wait for the minute tick).
                    pending = self._pending_collect
                    if pending:
                        self.after(500, lambda k=pending: self._run_collect(k, auto=False))
                    elif self.settings.get("auto_collect") and self._due_slot() is not None:
                        self.after(500, lambda: self._auto_tick(reschedule=False))
                else:
                    self.log(line)
        except queue.Empty:
            pass
        self.after(150, self._drain_output)

    def _log_collect_result(self):
        """After a scrape: confirm the 3-step roadmap and how many today's jobs are on the board."""
        try:
            today = date.today().isoformat()
            row = self.conn.execute(
                "SELECT COUNT(*) n, SUM(relevant=1) rel FROM jobs "
                "WHERE is_active=1 AND substr(COALESCE(first_seen_at, published_at), 1, 10)=?",
                (today,)).fetchone()
            shown = len(self.jobs.get_children())
            self.log(
                f"✓ Collection roadmap done:\n"
                f"  1) Scraped company boards (companies.csv) → local DB {db.DB_PATH}\n"
                f"  2) Jobs list updated: {shown} shown (Posted=today) · "
                f"{row['n']} first seen today ({row['rel'] or 0} relevant)\n"
                f"  3) Syncing to Supabase …\n")
        except Exception as e:
            self.log(f"! Could not summarise the collection: {str(e)[:120]}\n")

    def _sync_after_collect(self):
        """Push the local job DB to Supabase after every collection (and refresh the board).
        Runs in a background thread so the window stays usable."""
        def work():
            try:
                import sync_db
                conn = db.connect()
                _new, _upd, pushed = sync_db.sync(conn, quiet=True)
                conn.close()
                self.ui_q.put(lambda: (
                    self.log(f"3) Synced to Supabase: {pushed} job(s) uploaded.\n"),
                    self.refresh_all()))
            except Exception as e:
                msg = str(e)[:200]
                self.ui_q.put(lambda: self.log(
                    f"! Supabase sync failed ({msg}) - click 'Sync to DB' to retry.\n"))
        threading.Thread(target=work, daemon=True).start()

    def _reopen_false_negatives(self):
        """Move Data Analyst / IT Specialist / Solutions Engineer etc. out of 'not relevant'
        when the current rules say YES (fixes older AI/rule rejections).

        Does not auto-start Fetch missing details — use that Jobs-tab button when you want."""
        try:
            from relevance import RelevanceFilter
            n = RelevanceFilter(ai=None, log=lambda *a: None).reopen_false_negatives(self.conn)
            if n:
                self.log(f"Relevance: reopened {n} job(s) now marked relevant "
                         f"(Data/IT/Solutions/Analyst titles).\n")
                self.refresh_all()
            missing = self.conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 AND details_at IS NULL"
            ).fetchone()["c"]
            if missing:
                self.log(f"{missing} relevant job(s) still have no details (cannot Apply yet) — "
                         f"use “Fetch missing details” when ready.\n")
        except Exception as e:
            self.log(f"! Could not reopen relevance false-negatives: {str(e)[:150]}\n")

    def _sort_unsorted(self):
        """A collection that was stopped ("Stop now" ends it at once) leaves scraped jobs that
        are not sorted yet, and the Jobs tab only lists relevant ones. Sort them by rules
        (fast, no AI) so they show up; unclear titles are kept and the AI sorts them at the
        next collection."""
        try:
            from relevance import RelevanceFilter
            todo = db.jobs_needing_relevance(self.conn)
            todo = [r for r in todo if self.conn.execute(
                "SELECT relevant FROM jobs WHERE id=?", (r["id"],)).fetchone()["relevant"] is None]
            rf = RelevanceFilter(ai=None, log=lambda *a: None)
            if todo:
                decisions = rf.classify(
                    [(r["id"], r["title"], r["dept"]) for r in todo])
                for jid, (relevant, method) in decisions.items():
                    db.set_relevance(self.conn, jid, relevant, method)
                self.conn.commit()
                yes = sum(1 for r, _ in decisions.values() if r)
                self.log(f"Sorted {len(decisions)} scraped jobs by rules: {yes} relevant "
                         f"(unclear titles are kept; the AI sorts them at the next collection).\n")
            rf.reopen_false_negatives(self.conn)
        except Exception as e:
            self.log(f"! Could not sort the scraped jobs: {str(e)[:150]}\n")

    def refresh_all(self):
        # answers for every job, computed once per refresh (thousands of jobs)
        self.resolved = bank_mod.resolve_all(self.conn, self.bank)
        self.load_jobs()
        self.load_profile()
        self.load_bank_list()
        self.update_summary()
        if self.selected_job:
            self.show_job()

    def destroy(self):
        self.stop()
        super().destroy()


if __name__ == "__main__":
    App().mainloop()
