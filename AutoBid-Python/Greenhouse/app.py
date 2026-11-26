"""AutoBid · Greenhouse desktop app.

    python app.py   (or AutoBid-Greenhouse.bat)

Flow:
  1. Collect jobs from companies.csv (public Greenhouse boards API — no login)
  2. Fill Answers / My profile
  3. Open mail browser once → sign in to Gmail used on applications
  4. Make resume (optional) → Apply (Greenhouse Chrome + mail Chrome for security codes)
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

from config import BASE_DIR, BATCH_SIZE, PAUSE_FLAG, STOP_FLAG
import answers as bank_mod
import ai_settings
import db

PY = sys.executable.replace("pythonw.exe", "python.exe")
SETTINGS_PATH = BASE_DIR / "data" / "app_settings.json"

STATUS_COLORS = {"applied": "#1a7f37", "needs_you": "#b35900", "failed": "#c62828",
                 "skipped": "#777777", "ready": "#1f4fbf", "new": "#555555",
                 "1Interview": "#7b1fa2", "2Interview": "#6a1b9a", "3Interview": "#4a148c",
                 "Offer": "#00796b", "Rejected": "#8d6e63"}
POSTED_WITHIN = {"all": None, "today": 0, "3d ago": 3, "1w ago": 7, "1m ago": 30}

# Auto-collect clock (US Eastern). Same slots as Ashby (no 1:00 AM).
AUTO_COLLECT_HOURS_ET = (4, 8, 10, 12, 14, 16, 18, 20)
AUTO_SLOTS_LABEL = "4:00 · 8:00 · 10:00 · 12:00 · 14:00 · 16:00 · 18:00 · 20:00 ET"
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = timezone(timedelta(hours=-5))  # EST fallback if tzdata missing


def format_posted(raw):
    """Show only the age (“1 hour ago”), not the “Posted · …” prefix."""
    t = (raw or "").strip()
    t = re.sub(r"^(posted|viewed)\s*[·•\-:]*\s*", "", t, flags=re.I).strip()
    return t[:24] if t else ""


def posted_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def job_fresh_date(row):
    dates = [d for d in (posted_date(row["published_at"]), posted_date(row["first_seen_at"])) if d]
    return max(dates) if dates else None


def posted_label(d, today):
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
    for _ in range(3):
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
    s.setdefault("last_collect", None)
    s.setdefault("auto_last_slot", None)
    if "auto_kind" not in s:
        # Auto-collection on by default: today's jobs at the fixed Eastern clock times.
        s.update(auto_collect=True, auto_kind="today", auto_last_slot=None)
        save_settings(s)
    s.setdefault("auto_kind", "today")
    return s


def save_settings(s):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


class CompanyPicker(tk.Toplevel):
    """Multi-select list of companies with search, Select all / Clear (Ashby-style)."""

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
        self.title("AutoBid · Greenhouse")
        self.geometry("1280x800")
        self.minsize(1000, 640)
        self.conn = db.connect()
        try:
            self.bank = bank_mod.load_bank()
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception as e:
            self.withdraw()
            messagebox.showerror("Supabase", f"Could not load answer bank:\n{e}")
            raise SystemExit(1)
        self.proc = None
        self.proc_label = ""
        self.settings = load_settings()
        self.out_q = queue.Queue()
        self.ui_q = queue.Queue()
        self.resolved = {}
        self._checked = set()
        self.current_id = None
        self.selected_job = None
        self._pending_collect = None   # "today" | "all" queued while Apply/etc. runs
        self._auto_busy_logged = False
        self.pending_answers = {}      # key -> answer; flushed on tab change / Sync answers

        self._style()
        self._header()

        # Tabs + log share height (Ashby-style); jobs list gets a real scrollbar
        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tabs = ttk.Notebook(body)
        body.add(self.tabs, weight=4)
        self._jobs_tab()
        self._answers_tab()
        self._run_tab()
        self._ai_tab()
        self.tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        logf = ttk.LabelFrame(body, text="Log", padding=6)
        body.add(logf, weight=1)
        self.logbox = ScrolledText(logf, height=8, font=("Consolas", 9), wrap="word")
        self.logbox.pack(fill="both", expand=True)

        self.refresh_all()
        self._auto_info()
        self.after(150, self._drain)
        self.after(10_000, self._auto_tick)  # first auto-collect check 10 s after start
        self.protocol("WM_DELETE_WINDOW", self._on_close)

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
        """Title row + Ashby-style orange run bar (status + Stop after / Pause / Stop now)."""
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="AutoBid · Greenhouse", font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Label(top, text="  my.greenhouse.io · Remote", foreground="#666").pack(side="left")
        ttk.Button(top, text="Open bot browser", command=self.open_bot_browser).pack(
            side="left", padx=(16, 0))
        ttk.Button(top, text="Open mail browser", command=self.open_mail_browser).pack(
            side="left", padx=(8, 0))
        self.stats = ttk.Label(top, text="", foreground="#444")
        self.stats.pack(side="right")
        self.auto_hdr = ttk.Label(top, text="", foreground="#1a7f37")
        self.auto_hdr.pack(side="right", padx=16)
        self._header_frame = top

        # Shown on every tab while Collect / Make resume / Apply is running
        self.run_bar = tk.Frame(self, bg="#fff4e5", highlightthickness=1,
                                highlightbackground="#f0c27a")
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
        tk.Label(self.run_bar, text="", bg="#fff4e5").pack(side="left", fill="x", expand=True)
        self.b_stop_now = tk.Button(
            self.run_bar, text="■  Stop now", command=self.stop,
            bg="#c62828", fg="white", activebackground="#a51f1f", activeforeground="white",
            relief="flat", padx=14, pady=3, font=("Segoe UI Semibold", 10), cursor="hand2")
        self.b_stop_now.pack(side="right", padx=(6, 10), pady=4)
        self.b_pause = tk.Button(
            self.run_bar, text="⏸  Pause", command=self.toggle_pause,
            bg="#ffffff", fg="#7a4a00", relief="solid", bd=1, padx=12, pady=2,
            font=("Segoe UI Semibold", 10), cursor="hand2")
        self.b_pause.pack(side="right", padx=(6, 0), pady=4)
        self.b_stop_after = tk.Button(
            self.run_bar, text="Stop after this job", command=self.stop_after,
            bg="#ffffff", fg="#7a4a00", relief="solid", bd=1, padx=12, pady=2,
            font=("Segoe UI", 10), cursor="hand2")
        self.b_stop_after.pack(side="right", pady=4)

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
        self.company_btn = ttk.Button(bar, text="Companies: all ▾", width=24,
                                      command=self.pick_companies)
        self.company_btn.pack(side="left", padx=(0, 12))
        ttk.Label(bar, text="Posted").pack(side="left")
        self.f_posted = ttk.Combobox(bar, width=9, state="readonly", values=list(POSTED_WITHIN))
        self.f_posted.set("all")
        self.f_posted.pack(side="left", padx=(4, 6))
        self.f_posted.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Button(bar, text="Today's jobs", style="Accent.TButton",
                   command=self.show_todays_jobs).pack(side="left", padx=(0, 12))
        ttk.Button(bar, text="Collect jobs", command=self.collect).pack(side="right")
        ttk.Button(bar, text="Collect today's jobs", command=self.collect_today).pack(
            side="right", padx=4)
        ttk.Button(bar, text="Fetch", command=self.fetch_details).pack(side="right", padx=4)

        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Answers").pack(side="left")
        self.f_answers = ttk.Combobox(bar, width=28, state="readonly", values=[
            # "✓ all (ready to apply)" includes jobs whose only gaps AI will write (salary, essays, …)
            "any", "✓ all (ready to apply)", "missing answers", "AI essay needed", "no details yet"])
        self.f_answers.set("any")
        self.f_answers.pack(side="left", padx=(4, 12))
        self.f_answers.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Resume").pack(side="left")
        self.f_resume = ttk.Combobox(bar, width=13, state="readonly",
                                     values=["any", "✓ ready", "not made yet"])
        self.f_resume.set("any")
        self.f_resume.pack(side="left", padx=(4, 12))
        self.f_resume.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Search").pack(side="left")
        self.f_search = ttk.Entry(bar, width=30)
        self.f_search.pack(side="left", padx=4)
        self.f_search.bind("<KeyRelease>", lambda e: self.load_jobs())
        self.job_count = ttk.Label(bar, text="", style="Stat.TLabel")
        self.job_count.pack(side="left", padx=12)

        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)

        left = ttk.Frame(split)
        cols = ("chk", "status", "company", "title", "posted", "location", "pay", "ready",
                "resume", "apply", "rel")
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
        self.posted_dates = {}
        for c, label, w in [
                ("status", "Status", 75), ("company", "Company", 90), ("title", "Title", 200),
                ("posted", "Posted", 70), ("location", "Location", 110), ("pay", "Pay", 100),
                ("ready", "Answers", 90), ("resume", "Resume", 52), ("apply", "Apply", 72),
                ("rel", "Relevant", 70)]:
            self.job_headings[c] = label
            self.jobs.heading(c, text=label, command=lambda c=c: self.sort_jobs(c, toggle=True))
            self.jobs.column(c, width=w, anchor="w",
                             stretch=c in ("title", "location"))
        for st, col in STATUS_COLORS.items():
            self.jobs.tag_configure(st, foreground=col)
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
        ttk.Button(selbar, text="Select all shown", command=self.select_all_jobs).pack(side="right")
        ttk.Button(selbar, text="Clear selection", command=self.clear_job_selection).pack(
            side="right", padx=4)
        self.sel_info = ttk.Label(selbar, text="", foreground="#1f4fbf", justify="left")
        self.sel_info.pack(side="left", fill="x", expand=True)
        self.sel_info.bind("<Configure>",
                           lambda e: self.sel_info.config(wraplength=max(120, e.width - 4)))
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(0, 2))
        ttk.Button(btns, text="Open job page", command=self.open_job).pack(side="left")
        ttk.Button(btns, text="Open Apply link", command=self.open_apply).pack(side="left", padx=4)
        self.b_make = ttk.Button(btns, text="Make resume", command=self.make_resume_selected)
        self.b_make.pack(side="left", padx=4)
        ttk.Button(btns, text="Open resume", command=self.open_resume).pack(side="left", padx=4)
        btns2 = ttk.Frame(right)
        btns2.pack(fill="x", pady=(0, 6))
        self.b_dry = ttk.Button(btns2, text="Dry run", command=lambda: self.apply(True))
        self.b_dry.pack(side="left")
        self.b_apply = ttk.Button(btns2, text="Apply", style="Accent.TButton",
                                  command=lambda: self.apply(False))
        self.b_apply.pack(side="left", padx=4)
        self.b_answers_sel = ttk.Button(btns2, text="Answers for selected",
                                        command=self.show_answers_for_selected)
        self.b_answers_sel.pack(side="left")
        stbar = ttk.Frame(right)
        stbar.pack(fill="x", pady=(0, 6))
        ttk.Label(stbar, text="Status").pack(side="left")
        self.d_status = ttk.Combobox(stbar, width=12, state="readonly", values=db.STATUSES)
        self.d_status.pack(side="left", padx=4)
        ttk.Button(stbar, text="Set status", command=self.set_status_selected).pack(side="left")
        st_hint = ttk.Label(stbar, text="ticked jobs, else the one shown",
                            style="Stat.TLabel", justify="left")
        st_hint.pack(side="left", padx=6, fill="x", expand=True)

        self.d_tabs = ttk.Notebook(right)
        self.d_tabs.pack(fill="both", expand=True)
        self.d_overview = ScrolledText(self.d_tabs, wrap="word", font=("Segoe UI", 10), padx=8,
                                       pady=6, relief="flat", borderwidth=0)
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

    def _answers_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.answers_tab = tab
        self.tabs.add(tab, text="  Answers  ")
        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)

        box = ttk.LabelFrame(split, text="  My profile (Greenhouse)  ", padding=10)
        self.p_entries = {}
        fields = [
            ("name", "Full Name"), ("first_name", "First Name"), ("last_name", "Last Name"),
            ("email", "Email"), ("phone", "Phone"),
            ("location", "Location (City, State, Country)"),
            ("address", "Mailing address"),
            ("salary", "Desired / target salary (USD)"),
            ("linkedin", "LinkedIn URL"), ("github", "GitHub URL"), ("website", "Website"),
            ("authorized_us", "Authorized to work in US (true/false)"),
            ("sponsorship", "Need visa sponsorship (true/false)"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(box, width=36)
            e.grid(row=i, column=1, sticky="ew", padx=8, pady=2)
            e.insert(0, self.bank["profile"].get(key) or "")
            self.p_entries[key] = e
        box.columnconfigure(1, weight=1)
        ttk.Button(box, text="Save profile", style="Accent.TButton",
                   command=self.save_profile).grid(row=len(fields), column=1, sticky="e", pady=8)
        self.p_saved = ttk.Label(box, text="", foreground="#1a7f37")
        self.p_saved.grid(row=len(fields) + 1, column=1, sticky="e")
        ttk.Label(box, foreground="#666", wraplength=320, text=(
            "Profile is shared via Supabase keys; answer bank rows use platform=greenhouse "
            "(independent of Ashby)."
        )).grid(row=len(fields) + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        split.add(box, weight=1)

        right = ttk.Frame(split, padding=(10, 0, 0, 0))
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Required questions (Greenhouse bank)", style="H.TLabel").pack(
            side="left")
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
        self.answers_filter_ids = None
        self.answers_filter_keys = None
        self.q_filter_bar = ttk.Frame(right)
        self.q_filter_lbl = ttk.Label(self.q_filter_bar, text="", foreground="#1f4fbf")
        self.q_filter_lbl.pack(side="left")
        ttk.Button(self.q_filter_bar, text="Show all answers",
                   command=self.clear_answers_job_filter).pack(side="right")

        sortbar = ttk.Frame(right)
        sortbar.pack(fill="x", pady=(2, 0))
        ttk.Label(sortbar, text="Sort by", style="Stat.TLabel").pack(side="left")
        self.q_sort_pick = ttk.Combobox(
            sortbar, width=14, state="readonly",
            values=["Required in", "Type", "Question", "Answer"])
        self.q_sort_pick.set("Required in")
        self.q_sort_pick.pack(side="left", padx=4)
        self.q_sort_pick.bind("<<ComboboxSelected>>", lambda e: self._q_sort_from_picker())
        self.q_sort_dir_btn = ttk.Button(sortbar, text="▼ High→Low", width=12,
                                         command=self._q_toggle_sort_dir)
        self.q_sort_dir_btn.pack(side="left", padx=4)
        ttk.Label(sortbar, text="Search", style="Stat.TLabel").pack(side="left", padx=(16, 0))
        self.q_search = ttk.Entry(sortbar, width=28)
        self.q_search.pack(side="left", padx=4)
        self.q_search.bind("<KeyRelease>", self._on_q_search)
        self.q_search.bind("<Escape>", lambda e: self._clear_q_search())
        self._q_search_after = None

        self.q_count = ttk.Label(right, text="", style="Stat.TLabel")
        self.q_count.pack(anchor="w")
        qframe = ttk.Frame(right)
        cols = ("jobs", "type", "question", "answer")
        self.qlist = ttk.Treeview(qframe, columns=cols, show="headings", selectmode="browse",
                                  height=12)
        self.q_headings = {
            "jobs": "Required in", "type": "Type", "question": "Question", "answer": "Answer"}
        self.q_sort_col, self.q_sort_desc = "jobs", True
        self._q_row_meta = {}
        for c, label, w in [("jobs", "Required in", 80), ("type", "Type", 100),
                            ("question", "Question", 360), ("answer", "Answer", 180)]:
            self.qlist.heading(c, text=label,
                               command=lambda c=c: self.sort_questions(c, toggle=True))
            self.qlist.column(c, width=w, stretch=c == "question")
        self.qlist.tag_configure("open", foreground="#c62828")
        qsb = ttk.Scrollbar(qframe, orient="vertical", command=self.qlist.yview)
        self.qlist.configure(yscrollcommand=qsb.set)
        qsb.pack(side="right", fill="y")
        self.qlist.pack(side="left", fill="both", expand=True)
        self.qlist.bind("<<TreeviewSelect>>", lambda e: self.edit_question())

        ed = ttk.LabelFrame(right, text="  Answer (local until Sync / tab change)  ", padding=8)
        ed.pack(side="bottom", fill="x")
        eb = ttk.Frame(ed)
        eb.pack(side="bottom", fill="x")
        ttk.Button(eb, text="⬆ Sync answers to DB",
                   command=lambda: self.flush_pending_answers(reason="Sync answers")).pack(
            side="right", padx=(8, 0))
        self.e_saved = ttk.Label(eb, text="", foreground="#1a7f37")
        self.e_saved.pack(side="right", padx=10)
        self.back_btn = ttk.Button(eb, text="← Back to job", command=self.back_to_job)
        self.e_question = ttk.Label(ed, text="Select a question above.", wraplength=700,
                                    justify="left")
        self.e_question.pack(anchor="w")
        self.e_note = ttk.Label(ed, text="", style="Stat.TLabel", wraplength=700, justify="left")
        self.e_note.pack(anchor="w")
        self.e_holder = ttk.Frame(ed)
        self.e_holder.pack(fill="x", pady=6)
        self._answer_ignore = False
        self._answer_save_after = None
        qframe.pack(fill="both", expand=True, pady=(6, 6))
        split.add(right, weight=3)
        self.selected_q = None
        self._update_q_headings()
        self.after(300, self.load_bank_list)

    def _run_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="  Run  ")
        ttk.Label(tab, text=(
            "1) Collect: boards API list → descriptions + apply-form questions (?questions=true)\n"
            "2) AI tab → resume platform (Claude Code / API / …) — independent of Ashby\n"
            "3) Fill Answers / My profile (Jobs Answers column updates after Collect)\n"
            "4) Open mail browser once → sign in to Gmail used on applications → close it\n"
            "5) Select jobs → Make resume → Apply (job-boards …/jobs/<id>)\n"
            "6) TWO Chromes: Greenhouse (form) + mail (8-char security codes from Gmail)"
        ), justify="left").pack(anchor="w")
        row = ttk.Frame(tab, padding=(0, 12, 0, 0))
        row.pack(fill="x")
        ttk.Button(row, text="Collect today's jobs", command=self.collect_today).pack(side="left")
        ttk.Button(row, text="Collect all companies", command=self.collect).pack(side="left", padx=4)
        ttk.Label(tab, text="While a task runs, use the orange status bar at the top "
                            "(Stop after this job / Pause / Stop now).",
                  foreground="#666", wraplength=900).pack(anchor="w", pady=(10, 0))
        self.busy = ttk.Label(tab, text="", foreground="#b35900")
        self.busy.pack(anchor="w", pady=8)

        auto = ttk.LabelFrame(tab, text="  Auto-collection (US Eastern)  ", padding=10)
        auto.pack(fill="x", pady=(16, 0))
        self.a_on = tk.BooleanVar(value=self.settings.get("auto_collect", False))
        ttk.Checkbutton(auto, text="Automatically run", variable=self.a_on,
                        command=self.save_auto).grid(row=0, column=0, sticky="w")
        self.a_kind = ttk.Combobox(auto, width=24, state="readonly",
                                   values=["Collect today's jobs", "Collect all companies"])
        self.a_kind.set("Collect all companies" if self.settings.get("auto_kind") == "all"
                        else "Collect today's jobs")
        self.a_kind.bind("<<ComboboxSelected>>", lambda e: self.save_auto())
        self.a_kind.grid(row=0, column=1, padx=6)
        ttk.Label(auto, text=f"at {AUTO_SLOTS_LABEL}  (while this app is open)").grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(6, 0))
        ttk.Button(auto, text="⟳ Collect today's jobs now",
                   command=self.collect_today).grid(row=0, column=4, padx=(24, 0))
        self.a_info = ttk.Label(auto, text="", foreground="#666", justify="left")
        self.a_info.grid(row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))
        ttk.Label(auto, text=(
            "Each run scrapes companies.csv via Greenhouse’s public boards API (no login). "
            "Apply still uses bot Chrome. If Apply is running, Collect is queued."
        ), foreground="#666", wraplength=900, justify="left").grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))

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
            params += list(db.APPLIED_STATUSES)
        elif status != "all":
            sql += " AND status=?"
            params.append(status)
        chosen = self.settings.get("companies") or []
        if chosen:
            sql += (f" AND COALESCE(NULLIF(company,''), org) IN "
                    f"({','.join('?' * len(chosen))})")
            params += chosen
        rows = self.conn.execute(
            sql + " ORDER BY COALESCE(published_at, first_seen_at) DESC", params).fetchall()
        if getattr(self, "company_btn", None):
            self.company_btn.config(
                text="Companies: all ▾" if not chosen else (
                    f"{chosen[0][:18]} ▾" if len(chosen) == 1
                    else f"Companies: {len(chosen)} ▾"))
        keep = self.jobs.selection()
        self.jobs.delete(*self.jobs.get_children())
        answers_filter = self.f_answers.get()
        max_age = POSTED_WITHIN[self.f_posted.get()]
        today = date.today()
        self.posted_dates = {}
        for r in rows:
            company = r["company"] or r["org"]
            loc = r["location"] or ""
            wpt = r["workplace_type"] or ""
            if term and term not in f"{company} {r['title']} {loc} {wpt}".lower():
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
                ready, state = (f"AI writes ({len(missing)})", "essay")
            else:
                ready, state = f"{len(missing)} missing", "missing"
            if answers_filter != "any":
                want = {"✓ all (ready to apply)": "all", "missing answers": "missing",
                        "AI essay needed": "essay", "no details yet": "no details"}[answers_filter]
                if want == "all":
                    if state not in ("all", "essay"):
                        continue
                elif want != state:
                    continue
            has_resume = "✓" if r["resume_path"] and os.path.isfile(r["resume_path"]) else ""
            resume_filter = self.f_resume.get()
            if (resume_filter == "✓ ready" and not has_resume) or (
                    resume_filter == "not made yet" and has_resume):
                continue
            apply = (r["apply_url"] or r["job_url"] or "").strip()
            if apply and "greenhouse.io" in apply:
                apply_cell = "…/" + apply.split("greenhouse.io/", 1)[-1][:40]
            else:
                apply_cell = (apply[:36] + "…") if len(apply) > 36 else (apply or "—")
            rel = {1: "yes", 0: "no"}.get(r["relevant"], "?")
            if r["relevance_by"]:
                rel += f" ({r['relevance_by']})"
            loc_show = loc[:40] if loc else (wpt or "—")
            self.jobs.insert("", "end", iid=r["id"], tags=(r["status"],), values=(
                "☐", r["status"], company, r["title"], posted_label(posted, today),
                loc_show, (r["compensation"] or "")[:50], ready, has_resume, apply_cell, rel))
        if self.sort_col:
            self.sort_jobs(self.sort_col)
        shown = len(self.jobs.get_children())
        today_n = sum(1 for d in self.posted_dates.values() if d == today)
        if self.f_posted.get() == "today":
            self.job_count.config(text=f"{shown:,} today's jobs shown")
        else:
            self.job_count.config(text=f"{shown:,} shown · {today_n:,} today in this list")
        self._checked = set()
        still_there = [i for i in keep if self.jobs.exists(i)]
        if still_there:
            self.jobs.selection_set(still_there)
        if self.current_id and self.jobs.exists(self.current_id):
            self._mark_current(self.current_id)
        self._sync_checks()
        self.update_summary()

    def pick_companies(self):
        """Multi-select company filter (same as Ashby)."""
        counts = [(r["company"], r["n"]) for r in self.conn.execute(
            "SELECT COALESCE(NULLIF(company,''), org) AS company, COUNT(*) n "
            "FROM jobs WHERE is_active=1 AND relevant=1 "
            "AND COALESCE(NULLIF(company,''), org) IS NOT NULL "
            "GROUP BY 1 ORDER BY n DESC, company")]

        def apply(selection):
            self.settings["companies"] = selection
            save_settings(self.settings)
            self.load_jobs()
            self.update_summary()

        CompanyPicker(self, counts, self.settings.get("companies") or [], apply)

    @staticmethod
    def _sort_key(col, value):
        if col == "pay":
            m = re.search(r"(\d[\d,.]*)\s*([KkMm]?)", value or "")
            if not m:
                return -1
            n = float(m.group(1).replace(",", ""))
            return n * {"k": 1e3, "m": 1e6}.get(m.group(2).lower(), 1)
        if col == "ready":
            return 0 if (value or "").startswith("✓") else int(re.sub(r"\D", "", value or "") or 99)
        return (value or "").lower()

    def sort_jobs(self, col, toggle=False):
        if toggle:
            self.sort_desc = (not self.sort_desc) if self.sort_col == col else False
            self.sort_col = col
        if col == "posted":
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

    def selected_ids(self):
        ticked = list(self.jobs.selection())
        if ticked:
            return ticked
        return [self.current_id] if self.current_id and self.jobs.exists(self.current_id) else []

    def select_all_jobs(self):
        self.jobs.selection_set(self.jobs.get_children())

    def clear_job_selection(self):
        self.jobs.selection_remove(self.jobs.selection())

    def toggle_all_shown(self):
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
            return None
        row = self.jobs.identify_row(event.y)
        if not row:
            return None
        if event.state & 0x0005:
            self.after(1, lambda: self.view_job(row))
            return None
        if self.jobs.identify_column(event.x) == "#1":
            self._toggle(row)
            if not self.current_id:
                self.view_job(row)
            return "break"
        self.view_job(row)
        return "break"

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
        self.sel_info.config(
            text=f"☑ {n} job{'s' if n != 1 else ''} ticked" if n else
            "Tick ☐ jobs to work on several at once (header ☐ = all shown)")

    def show_job(self, shown=None):
        shown = shown or self.current_id
        if not shown or not self.jobs.exists(shown):
            return
        job = self.conn.execute("SELECT * FROM jobs WHERE id=?", (shown,)).fetchone()
        if not job:
            return
        self.selected_job = job
        self.d_status.set(job["status"] or "")
        self.d_title.config(text=f"{job['title']}  ·  {job['company'] or job['org']}")
        locs = json.loads(job["locations_all"] or "[]") if job["locations_all"] else []
        meta = (f"{job['workplace_type'] or '—'}  ·  {job['location'] or '—'}\n"
                f"{', '.join(locs) if locs else ''}\n"
                f"{(job['compensation'] or '').replace(chr(10), '   ·   ')}\n"
                f"Status: {job['status']}" + (f"  —  {job['note']}" if job["note"] else ""))
        apply = job["apply_url"] or job["job_url"] or ""
        if apply:
            meta += f"\nApply: {apply}"
        self.d_meta.config(text=meta.strip())

        t = self.d_overview
        t.config(state="normal")
        t.delete("1.0", "end")
        desc = job["description"] or job["description_html"] or ""
        if job["description_html"] and not job["description"]:
            desc = re.sub(r"<[^>]+>", " ", job["description_html"])
        for line in desc.splitlines():
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
        if shown not in self.resolved:
            return
        for i in bank_mod.resolve_job(self.conn, job["id"], self.bank):
            q = i["q"]
            self.d_app_items[q["id"]] = i
            if i.get("error"):
                ans, tag = f"!! {i['error']}", "missing"
            elif i["missing"]:
                if bank_mod.ai_writable(i):
                    ans, tag = "(AI will write)", "ai"
                elif (i["source"] or "").startswith("profile"):
                    ans, tag = "MISSING - fill My profile", "missing"
                else:
                    ans, tag = "MISSING - need your answer", "missing"
            else:
                v = i["value"]
                ans = ("Yes" if v else "No") if isinstance(v, bool) else str(v)
                tag = ""
            self.d_app.insert("", "end", iid=q["id"],
                              values=(q["title"], ans[:200], i["source"] or ""), tags=(tag,))

    def answer_from_job(self, _event=None):
        sel = self.d_app.selection()
        item = self.d_app_items.get(sel[0]) if sel else None
        if not item:
            return
        q, source = item["q"], item["source"] or ""
        if source and source.startswith("tailored"):
            messagebox.showinfo("Resume", "The resume is tailored per job by Make resume / Apply.")
            return
        self.return_to_job = self.selected_job["id"] if self.selected_job else None
        self.back_btn.pack(side="left")
        self.tabs.select(self.answers_tab)
        if source.startswith("profile."):
            key = source.split(".", 1)[1]
            entry = self.p_entries.get(key)
            if entry:
                entry.focus_set()
                entry.select_range(0, "end")
                self.e_question.config(
                    text=f"\"{q['title']}\" comes from My profile — fill it and Save profile.")
                self.e_note.config(text="")
            return
        bk = q.get("bank_key") or db.bank_key(q.get("title") or "", q.get("type") or "String")
        idx = next((n for n, b in enumerate(self.bank["questions"]) if b["key"] == bk), None)
        if idx is None:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
            idx = next((n for n, b in enumerate(self.bank["questions"]) if b["key"] == bk), None)
            if idx is None:
                messagebox.showwarning("Answers", "This question is not in the answer bank yet.")
                return
        if not self.qlist.exists(str(idx)):
            self.q_only_open.set(False)
            self.q_kind.set("All required")
            self.load_bank_list()
        self.qlist.selection_set(str(idx))
        self.qlist.see(str(idx))
        self.qlist.focus(str(idx))

    def back_to_job(self):
        jid = getattr(self, "return_to_job", None)
        self.back_btn.pack_forget()
        self.tabs.select(0)
        self.d_tabs.select(1)
        if jid and self.jobs.exists(jid):
            self.view_job(jid)

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

    def set_status_selected(self):
        status = self.d_status.get()
        ids = self.selected_ids()
        if not ids or not status:
            return
        if len(ids) > 1 and not messagebox.askyesno(
                "Set status", f'Set status of {len(ids)} jobs to "{status}"?'):
            return
        for jid in ids:
            db.set_status(self.conn, jid, status)
        self.log(f'✓ Status "{status}": {len(ids)} job(s)\n')
        self.refresh_all()

    def show_answers_for_selected(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Answers for selected",
                                "Tick one or more jobs (or select a job on the right).")
            return
        try:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception as e:
            self.log(f"! Answer bank sync: {e}\n")
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT bank_key, COUNT(DISTINCT job_id) n FROM questions "
            f"WHERE required=1 AND job_id IN ({placeholders}) "
            f"AND bank_key IS NOT NULL AND bank_key != '' GROUP BY bank_key",
            ids).fetchall()
        self.answers_filter_ids = list(ids)
        self.answers_filter_keys = {r["bank_key"]: r["n"] for r in rows}
        titles = []
        for jid in ids[:3]:
            row = self.conn.execute(
                "SELECT title, company FROM jobs WHERE id=?", (jid,)).fetchone()
            if row:
                titles.append(f"{row['company'] or '?'}: {(row['title'] or '')[:40]}")
        more = f" (+{len(ids) - 3} more)" if len(ids) > 3 else ""
        self.q_filter_lbl.config(
            text=f"Answers for {len(ids)} job(s): " + "; ".join(titles) + more)
        self.q_filter_bar.pack(fill="x", pady=(4, 0), before=self.q_count)
        self.q_only_open.set(False)
        self.q_kind.set("All required")
        self.tabs.select(self.answers_tab)
        self.load_bank_list()

    def clear_answers_job_filter(self):
        self.answers_filter_ids = None
        self.answers_filter_keys = None
        self.q_filter_bar.pack_forget()
        self.load_bank_list()

    def import_answers_backup(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Choose autobid_questions backup (JSON or CSV)",
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
        messagebox.showinfo(
            "Import from backup",
            f"Filled: {stats['filled']}\nSkipped (already answered): {stats['skipped_has_answer']}\n"
            f"No match: {stats['no_match']}")

    def update_summary(self):
        rows = dict(self.conn.execute(
            "SELECT status, COUNT(*) FROM jobs WHERE is_active=1 AND relevant=1 GROUP BY status"
        ).fetchall())
        applied = sum(rows.get(s, 0) for s in db.APPLIED_STATUSES)
        total = sum(rows.values())
        self.stats.config(
            text=f"{total} jobs · {applied} applied · {rows.get('needs_you', 0)} need you")

    def refresh_all(self):
        try:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception:
            pass
        self.resolved = bank_mod.resolve_all(self.conn, self.bank)
        self.load_jobs()
        if getattr(self, "qlist", None):
            self.load_bank_list()
        self.update_summary()
        if self.selected_job:
            self.show_job()

    def show_todays_jobs(self):
        self.f_relevant.set("relevant")
        self.f_status.set("all")
        self.f_answers.set("any")
        self.f_resume.set("any")
        self.f_posted.set("today")
        self.load_jobs()
        self.tabs.select(0)

    def collect_today(self, auto=False):
        return self._start_or_queue_collect("today", auto=auto)

    def collect(self, auto=False):
        return self._start_or_queue_collect("all", auto=auto)

    def fetch_details(self):
        """Re-fetch description + apply-form questions (answers) for relevant jobs.

        Prefers ticked jobs; otherwise jobs still missing details/questions.
        Use Collect to discover new postings — Fetch refreshes form fields for Apply.
        """
        ids = [i for i in self.jobs.selection()] if getattr(self, "jobs", None) else []
        # Fall back to header ticks if selection empty but _checked set
        if not ids and getattr(self, "_checked", None):
            ids = [i for i in self._checked if self.jobs.exists(i)]
        if ids:
            n = len(ids)
            if not messagebox.askyesno(
                    "Fetch",
                    f"Re-fetch description + application questions for {n} ticked job(s)?\n\n"
                    "This updates the Answers column and answer bank from the boards API."):
                return False
            path = self._write_selection(ids)
            return self.run_script(
                ["scrape_details.py", "--jobs-file", str(path), "--refresh"],
                f"Fetching details + questions: {n} job(s)")
        n = self.conn.execute("""
            SELECT COUNT(*) c FROM jobs j
            WHERE j.is_active=1 AND j.relevant=1 AND (
              j.description IS NULL OR j.description='' OR j.details_at IS NULL OR
              NOT EXISTS (SELECT 1 FROM questions q WHERE q.job_id = j.id)
            )""").fetchone()["c"]
        if n:
            if not messagebox.askyesno(
                    "Fetch",
                    f"{n} relevant job(s) still need description and/or questions "
                    f"(Answers = “no details yet”).\n\nFetch them now from the boards API?"):
                return False
            return self.run_script(
                ["scrape_details.py"],
                f"Fetching details + questions: {n} job(s)")
        # Nothing missing — offer full refresh of all relevant
        total = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1"
        ).fetchone()["c"]
        if not total:
            messagebox.showinfo("Fetch", "No relevant jobs yet — run Collect first.")
            return False
        if not messagebox.askyesno(
                "Fetch",
                f"Every relevant job already has details.\n\n"
                f"Re-fetch description + questions for all {total} relevant job(s)?"):
            return False
        return self.run_script(
            ["scrape_details.py", "--refresh"],
            f"Re-fetching details + questions: {total} job(s)")

    def _start_or_queue_collect(self, kind, auto=False):
        """Start a collection now, or queue it if Apply / another task is running."""
        busy = self.proc and self.proc.poll() is None
        if busy:
            if self._pending_collect != "all":
                self._pending_collect = kind
            label = "today's jobs" if kind == "today" else "all companies"
            if not auto or not self._auto_busy_logged:
                self.log(f"(collection queued: {label} — starts when the current task finishes)\n")
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
                                 "Collecting today's jobs" + (" (auto)" if auto else ""),
                                 quiet=auto)
            if ok:
                self.f_posted.set("today")
        else:
            ok = self.run_script(["collect.py"],
                                 "Collecting Greenhouse boards (companies.csv)"
                                 + (" (auto)" if auto else ""),
                                 quiet=auto)
            if ok:
                self.f_posted.set("all")
        if ok:
            self._mark_auto_slot_covered()
        return ok

    def _show_queue_hint(self):
        kind = self._pending_collect
        if not kind:
            return
        label = "Collect today's jobs" if kind == "today" else "Collect all companies"
        try:
            self.run_label.config(
                text=f"{self.run_label.cget('text').split(' · queued')[0]} · queued: {label}")
        except Exception:
            pass

    def _mark_auto_slot_covered(self):
        """After any collection starts, mark the current Eastern slot so 'next' stays fixed."""
        slot = latest_passed_slot()
        self.settings["auto_last_slot"] = slot.isoformat()
        save_settings(self.settings)
        self._auto_info()

    def refresh(self):
        self.refresh_all()

    def open_job(self):
        if self.selected_job and self.selected_job.get("job_url"):
            webbrowser.open(self.selected_job["job_url"])
            return
        ids = self.selected_ids()
        if not ids:
            return
        row = self.conn.execute("SELECT job_url FROM jobs WHERE id=?", (ids[0],)).fetchone()
        if row and row["job_url"]:
            webbrowser.open(row["job_url"])

    def open_apply(self):
        ids = self.selected_ids()
        if not ids:
            return
        row = self.conn.execute(
            "SELECT id, org, apply_url, job_url FROM jobs WHERE id=?", (ids[0],)
        ).fetchone()
        if not row:
            return
        url = (row["apply_url"] or row["job_url"] or "").strip()
        # Rebuild canonical job-boards link when possible (same as bid.py)
        sid = str(row["id"] or "")
        org = (row["org"] or "").strip()
        if ":" in sid:
            token, jid = sid.split(":", 1)
            org = org or token
            if org and jid.isdigit():
                url = f"https://job-boards.greenhouse.io/{org}/jobs/{jid}"
        if url:
            self.log(f"Open Apply link: {url}\n")
            webbrowser.open(url)
        else:
            messagebox.showinfo("Apply link", "No Apply link remembered for this job yet.")

    def save_profile(self):
        try:
            vals = {k: e.get().strip() for k, e in self.p_entries.items()}
            self.bank = bank_mod.set_profile(vals, self.bank)
        except Exception as e:
            messagebox.showerror("Not saved", str(e))
            return
        self.log("✓ Profile saved.\n")
        self.refresh_all()
        if getattr(self, "p_saved", None):
            self.p_saved.config(text=f"✓ Saved  {datetime.now().strftime('%H:%M:%S')}")

    def open_bot_browser(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("Busy", "Stop the current task first.")
            return
        try:
            from browser import get_profile_dir
            import open_browser_login
            open_browser_login.main()
            self.log(f"Opened Greenhouse Chrome ({get_profile_dir()}) — "
                     f"sign in to MyGreenhouse, then close it before Collect/Apply "
                     f"(Ashby can keep its own Chrome open).\n")
        except Exception as e:
            messagebox.showerror("Open bot browser", str(e))

    def open_mail_browser(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("Busy", "Stop the current task first.")
            return
        try:
            from browser import get_mail_profile_dir
            import open_browser_mail
            open_browser_mail.main()
            self.log(f"Opened mail Chrome ({get_mail_profile_dir()}) — "
                     f"sign in to Gmail (same address as on applications), "
                     f"then close it before Apply. Apply will reopen Greenhouse + mail "
                     f"together to read security codes.\n")
        except Exception as e:
            messagebox.showerror("Open mail browser", str(e))

    def make_resume_selected(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Make resume", "Select one or more jobs first.")
            return
        self._flush_ai_settings()
        path = self._write_selection(ids)
        self.run_script(["make_resume.py", "--jobs-file", str(path)],
                        f"Making resumes: {len(ids)} jobs")

    def apply(self, dry):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Apply", "Select one or more jobs first.")
            return
        if not dry and not messagebox.askyesno(
                "Apply",
                f"Submit applications to {len(ids)} Greenhouse job(s)?\n\n"
                "Apply opens Greenhouse Chrome + mail Chrome.\n"
                "Sign in to Gmail first via “Open mail browser” if you have not yet."):
            return
        self._flush_ai_settings()
        path = self._write_selection(ids)
        args = ["bid.py", "--jobs-file", str(path), "--batch", str(BATCH_SIZE)]
        if dry:
            args.append("--dry-run")
        self.run_script(args, f"{'Dry run' if dry else 'Applying'}: {len(ids)} jobs")

    def _write_selection(self, ids):
        path = BASE_DIR / "data" / "selection.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ids), encoding="utf-8")
        return path

    def _show_run_bar(self, label):
        self.run_stats = {"ok": 0, "fail": 0, "need": 0}
        self.run_label.config(text=f"●  Running: {label}", bg="#fff4e5", fg="#7a4a00")
        self.run_bar.config(bg="#fff4e5")
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
        # Cool-down between batches — still running (not stopped)
        if t.startswith("batch pause"):
            self.run_label.config(
                text=f"●  Running: {self.proc_label}  ·  {t}",
                bg="#fff4e5", fg="#7a4a00")
            return
        # success — only after URL change (not merely clicking Submit)
        if ("✓ applied" in t or "✓ submitted" in t
                or t == "APPLIED" or t.startswith("APPLIED:")
                or t.startswith("APPLIED (by you)")):
            self.run_stats["ok"] += 1
        # need you (OTP / URL unchanged / upload / button missing / tab left open)
        elif ("? email security code" in t
              or "? submit clicked" in t
              or "URL unchanged" in t
              or "required fields still empty" in t
              or "submit blocked" in t
              or "resume upload failed" in t
              or "Submit application button not found" in t
              or t.startswith("UNCLEAR")
              or t.startswith("NOT SUBMITTED")
              or ("tab left open" in t and t.lstrip().startswith(("!", "?")))):
            self.run_stats["need"] += 1
        # failed (no apply link, etc.)
        elif (t.startswith("FAILED") or "FAILED -" in t
              or "no Apply link remembered" in t
              or t.startswith("failed:") or t.startswith("NOT applied")):
            self.run_stats["fail"] += 1
        else:
            return
        self.run_label.config(text=f"●  Running: {self.proc_label}", bg="#fff4e5", fg="#7a4a00")
        self._update_run_stats()
        try:
            self.update_summary()
        except Exception:
            pass

    def _set_pause_button(self, paused):
        if paused:
            self.b_pause.config(text="▶  Continue", bg="#1a7f37", fg="white",
                                activebackground="#14622a", activeforeground="white")
        else:
            self.b_pause.config(text="⏸  Pause", bg="#ffffff", fg="#7a4a00",
                                activebackground="#f5f5f5", activeforeground="#7a4a00")

    def toggle_pause(self):
        """Pause: finish the current step, then wait. Continue: resume."""
        if not (self.proc and self.proc.poll() is None):
            return
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink(missing_ok=True)
            self._set_pause_button(paused=False)
            self.run_label.config(text=f"●  Running: {self.proc_label}", bg="#fff4e5", fg="#7a4a00")
            self.run_bar.config(bg="#fff4e5")
            self.log("\n▶ Continued.\n")
        else:
            PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            PAUSE_FLAG.write_text("pause", encoding="utf-8")
            self._set_pause_button(paused=True)
            self.run_label.config(
                text=f"⏸  Paused: {self.proc_label}  (finishes current step, then waits)")
            self.log("\n⏸ Paused — finishes the current step, then waits. "
                     "Press ▶ Continue to resume, or Stop now to end.\n")

    def stop_after(self):
        """Finish the current job/card cleanly, then stop (STOP_FLAG)."""
        if not (self.proc and self.proc.poll() is None):
            return
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text("stop", encoding="utf-8")
        self.b_stop_after.config(text="stopping after this job …", state="disabled")
        self.log("\nWill stop after the current job (press Stop now to end at once).\n")

    def stop(self):
        """Stop now: kill the task and its Chrome / AI child processes."""
        if not (self.proc and self.proc.poll() is None):
            return
        PAUSE_FLAG.unlink(missing_ok=True)
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text("stop", encoding="utf-8")
        pid = self.proc.pid
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=20)
            else:
                self.proc.kill()
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self._set_pause_button(paused=False)
        self.log("\n■ Stopped now (task, browser, and AI call ended).\n")

    def _ensure_chrome_free(self, auto=False, also_mail=False):
        """Apply needs exclusive .browser-profile-greenhouse (+ mail when bidding)."""
        try:
            from browser import close_bot_chrome, get_profile_dir, get_mail_profile_dir, profile_busy
        except Exception:
            return True
        checks = [("greenhouse", get_profile_dir, None)]
        if also_mail:
            checks.append(("mail", get_mail_profile_dir, "mail"))
        for label, get_dir, bot in checks:
            if not profile_busy(bot):
                continue
            profile = get_dir()
            if auto:
                n = close_bot_chrome(bot)
                self.log(f"(auto) Closed {label} Chrome ({n} process(es)) → {profile.name}\n")
                import time as _t
                _t.sleep(0.8)
                if profile_busy(bot):
                    return False
                continue
            title = "Greenhouse Chrome is open" if bot is None else "Mail Chrome is open"
            hint = (
                "“Open bot browser” (or another Greenhouse task)"
                if bot is None else
                "“Open mail browser”"
            )
            if not messagebox.askyesno(
                    title,
                    f"{hint} is using:\n{profile}\n\n"
                    f"Close that Chrome now so Apply can run?\n\n"
                    "(Ashby’s Chrome is separate and will not be closed.)"):
                self.log(f"Cancelled — close {label} Chrome, then try again.\n")
                return False
            n = close_bot_chrome(bot)
            self.log(f"Closed {label} Chrome ({n} process(es)) → {profile.name}\n")
            import time as _t
            _t.sleep(0.8)
            if profile_busy(bot):
                return False
        return True

    def run_script(self, args, label, quiet=False):
        if self.proc and self.proc.poll() is None:
            if not quiet:
                messagebox.showwarning("Busy", "Something is already running. Stop it first.")
            return False
        # Apply needs Greenhouse + mail Chrome free; Make resume needs Greenhouse only
        name = Path(str(args[0])).name if args else ""
        if name == "bid.py":
            if not self._ensure_chrome_free(auto=quiet, also_mail=True):
                return False
        elif name == "make_resume.py":
            if not self._ensure_chrome_free(auto=quiet, also_mail=False):
                return False
        STOP_FLAG.unlink(missing_ok=True)
        PAUSE_FLAG.unlink(missing_ok=True)
        self.proc_label = label
        self.log(f"\n━━ {label} ━━\n")
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(
            [PY, "-u", *args], cwd=BASE_DIR, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.busy.config(text=f"● running: {label}")
        self._show_run_bar(label)

        def pump():
            for line in self.proc.stdout:
                self.out_q.put(line)
            self.proc.wait()
            self.out_q.put(None)
        threading.Thread(target=pump, daemon=True).start()
        return True

    def log(self, text):
        self.logbox.insert("end", text)
        self.logbox.see("end")
        self._track_run_line(text)

    # ---------------- auto-collection

    def save_auto(self):
        turned_on = bool(self.a_on.get()) and not self.settings.get("auto_collect")
        self.settings["auto_collect"] = bool(self.a_on.get())
        self.settings["auto_kind"] = ("all" if self.a_kind.get() == "Collect all companies"
                                      else "today")
        if turned_on:
            # Just enabled: run the current Eastern slot once (do not wait for the next clock).
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
        due = self._due_slot()
        return due if due else next_auto_slot(after)

    def _auto_info(self):
        last = self.settings.get("last_collect")
        last_txt = datetime.fromisoformat(last).strftime("%b %d %H:%M") if last else "never"
        what = ("today's jobs" if self.settings.get("auto_kind") != "all"
                else "all companies")
        if self.settings.get("auto_collect"):
            nxt = self._next_due()
            nxt_et = nxt.astimezone(ET)
            local = nxt.astimezone()
            due_now = self._due_slot() is not None
            nxt_txt = ("due now" if due_now else
                       f"{nxt_et.strftime('%b %d %H:%M')} ET"
                       + ("" if local.tzinfo == ET else f" ({local.strftime('%H:%M')} local)"))
            queued = ("   ·   queued after current task" if self._pending_collect else "")
            txt = (f"Last collection: {last_txt}   ·   auto: {what} at fixed ET times   ·   "
                   f"next: {nxt_txt}{queued}\nSlots: {AUTO_SLOTS_LABEL}")
            self.auto_hdr.config(
                text=f"auto-collect ({what}) · next {nxt_et.strftime('%H:%M')} ET"
                     + (" · due" if due_now else "")
                     + (" · queued" if self._pending_collect else ""))
        else:
            txt = f"Last collection: {last_txt}   ·   auto-collection is off"
            self.auto_hdr.config(text="")
        if getattr(self, "a_info", None):
            self.a_info.config(text=txt)

    def _auto_tick(self, reschedule=True):
        """Start a collection when a fixed Eastern slot is due (every minute while app is open)."""
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
                                 f"next slot {next_auto_slot(due).strftime('%H:%M')} ET)\n")
            self._auto_info()
        finally:
            if reschedule:
                self.after(60_000, self._auto_tick)

    def _drain(self):
        while not self.ui_q.empty():
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
                    self.log("━━ finished ━━\n")
                    if (finished_label.startswith("Collecting")
                            and self.proc and self.proc.returncode == 0):
                        self.settings["last_collect"] = datetime.now().isoformat(
                            timespec="seconds")
                        save_settings(self.settings)
                        self._auto_info()
                    if finished_label.startswith("Collecting"):
                        self.f_posted.set(
                            "today" if "today" in finished_label.lower() else self.f_posted.get())
                    # Collect / Fetch update questions → refresh Answers column
                    self.refresh_all()
                    # Queued Collect first; else start auto-collect if a slot is due
                    pending = self._pending_collect
                    if pending:
                        self.after(500, lambda k=pending: self._run_collect(k, auto=False))
                    elif self.settings.get("auto_collect") and self._due_slot() is not None:
                        self.after(500, lambda: self._auto_tick(reschedule=False))
                else:
                    self.log(line)
        except queue.Empty:
            pass
        self.after(150, self._drain)

    # ------------------------------------------------------------ Answers list (Greenhouse bank)

    def _update_q_headings(self):
        for c, label in self.q_headings.items():
            if self.q_sort_col == c:
                arrow = " ▼" if self.q_sort_desc else " ▲"
            else:
                arrow = " ⇅"
            self.qlist.heading(c, text=label + arrow)
        label = self.q_headings.get(self.q_sort_col or "jobs", "Required in")
        if getattr(self, "q_sort_pick", None):
            try:
                self.q_sort_pick.set(label)
            except Exception:
                pass
        if getattr(self, "q_sort_dir_btn", None):
            self.q_sort_dir_btn.config(
                text="▼ High→Low" if self.q_sort_desc else "▲ Low→High")

    def _q_sort_from_picker(self):
        name = self.q_sort_pick.get()
        col = next((c for c, lab in self.q_headings.items() if lab == name), "jobs")
        self.q_sort_col = col
        self.load_bank_list()

    def _q_toggle_sort_dir(self):
        self.q_sort_desc = not self.q_sort_desc
        self.load_bank_list()

    def sort_questions(self, col, toggle=False):
        if toggle:
            self.q_sort_desc = (not self.q_sort_desc) if self.q_sort_col == col else False
            self.q_sort_col = col
        self.load_bank_list()

    def _q_sort_key(self, row):
        col = self.q_sort_col
        if col == "jobs":
            return row["jobs_n"]
        if col == "type":
            return (row["type"] or "").lower()
        if col == "question":
            return (row["question"] or "").lower()
        if col == "answer":
            return (0 if row["empty"] else 1, (row["answer"] or "").lower())
        return 0

    def _on_q_search(self, _event=None):
        if self._q_search_after:
            try:
                self.after_cancel(self._q_search_after)
            except Exception:
                pass
        self._q_search_after = self.after(180, self.load_bank_list)

    def _clear_q_search(self):
        self.q_search.delete(0, "end")
        self.load_bank_list()
        return "break"

    def load_bank_list(self):
        kind = self.q_kind.get() if getattr(self, "q_kind", None) else "All required"
        filt = self.answers_filter_keys
        needle = (self.q_search.get() if getattr(self, "q_search", None) else "") or ""
        needle = needle.strip().lower()
        prev = self.qlist.selection()
        self.qlist.delete(*self.qlist.get_children())
        self._q_row_meta = {}
        shown = open_n = 0
        rows = []
        for i, q in enumerate(self.bank.get("questions") or []):
            if filt is not None:
                if q["key"] not in filt:
                    continue
            elif not q.get("asked_in_jobs"):
                continue
            # "You answer" = yes/no, choices, city, auth, multi-select, … (even if type=String)
            # "AI writes" = real free-form text / essays only
            is_ai = bank_mod.is_ai_text_question(q)
            if (kind == "You answer" and is_ai) or (kind.startswith("AI") and not is_ai):
                continue
            empty = bank_mod._is_empty(q.get("answer"))
            ans = "" if empty else (
                ", ".join(q["answer"]) if isinstance(q["answer"], list) else str(q["answer"]))
            if empty and is_ai:
                ans = "(AI writes per job)"
            elif empty:
                ans = "(need your answer)"
            if needle:
                blob = f"{q.get('question') or ''} {ans} {q.get('type') or ''}".lower()
                if needle not in blob:
                    continue
            open_n += empty
            if self.q_only_open.get() and not empty:
                continue
            if filt is not None:
                n_sel = filt.get(q["key"], 0)
                n_jobs = len(self.answers_filter_ids or [])
                jobs_col = f"{n_sel}/{n_jobs}"
                jobs_n = n_sel
            else:
                n = q["asked_in_jobs"]
                jobs_col = f"{n} job{'s' if n != 1 else ''}"
                jobs_n = int(n or 0)
            rows.append({
                "iid": str(i),
                "tags": ("open",) if empty else (),
                "values": (jobs_col, q.get("type") or "", q.get("question") or "", (ans or "")[:80]),
                "jobs_n": jobs_n,
                "type": q.get("type") or "",
                "question": q.get("question") or "",
                "answer": ans or "",
                "empty": empty,
            })
            shown += 1
        if self.q_sort_col:
            rows.sort(key=self._q_sort_key, reverse=self.q_sort_desc)
        for r in rows:
            self.qlist.insert("", "end", iid=r["iid"], tags=r["tags"], values=r["values"])
            self._q_row_meta[r["iid"]] = r
        self._update_q_headings()
        if prev:
            keep = [i for i in prev if self.qlist.exists(i)]
            if keep:
                self.qlist.selection_set(keep)
                self.qlist.see(keep[0])
        search_bit = f"  ·  search “{needle}”" if needle else ""
        if filt is not None:
            n_jobs = len(self.answers_filter_ids or [])
            self.q_count.config(
                text=f"{shown} questions for {n_jobs} selected job(s)  ·  {open_n} unanswered"
                     f"{search_bit}")
        else:
            self.q_count.config(
                text=f"{shown} questions shown  ·  {open_n} unanswered{search_bit}  ·  "
                     f"platform=greenhouse")

    def edit_question(self):
        sel = self.qlist.selection()
        if not sel:
            return
        q = self.bank["questions"][int(sel[0])]
        self.selected_q = q
        self.e_question.config(text=q.get("question") or "")
        if getattr(self, "e_note", None):
            self.e_note.config(text=q.get("note") or "")
        for w in self.e_holder.winfo_children():
            w.destroy()
        if self._answer_save_after:
            try:
                self.after_cancel(self._answer_save_after)
            except Exception:
                pass
            self._answer_save_after = None
        cur = q.get("answer")
        qtype = q.get("type") or "String"
        self._answer_ignore = True
        try:
            if qtype == "Boolean":
                self.e_widget = ttk.Combobox(self.e_holder, values=["Yes", "No"], state="readonly",
                                             width=20)
                self.e_widget.set(
                    "" if cur is None else ("Yes" if cur in (True, "Yes", "yes") else "No"))
                self.e_widget.pack(anchor="w")
                self.e_widget.bind("<<ComboboxSelected>>",
                                   lambda e: self._auto_save_answer())
            elif qtype == "ValueSelect":
                self.e_widget = ttk.Combobox(
                    self.e_holder, values=q.get("choices") or [], state="readonly", width=60)
                self.e_widget.set(cur or "")
                self.e_widget.pack(anchor="w")
                self.e_widget.bind("<<ComboboxSelected>>",
                                   lambda e: self._auto_save_answer())
            else:
                self.e_widget = tk.Text(self.e_holder, height=4 if qtype == "LongText" else 2,
                                        wrap="word", font=("Segoe UI", 10))
                self.e_widget.insert("1.0", cur or "")
                self.e_widget.pack(fill="x")
                self.e_widget.bind("<KeyRelease>", self._schedule_answer_save)
                self.e_widget.bind("<FocusOut>", lambda e: self._auto_save_answer())
        finally:
            self._answer_ignore = False

    def _schedule_answer_save(self, _event=None):
        if self._answer_ignore:
            return
        if self._answer_save_after:
            try:
                self.after_cancel(self._answer_save_after)
            except Exception:
                pass
        self._answer_save_after = self.after(500, self._auto_save_answer)

    def _auto_save_answer(self):
        if self._answer_ignore:
            return
        self.save_answer()

    def save_answer(self, clear=False):
        """Remember the answer in local cache + refresh the board (no Supabase yet)."""
        q = self.selected_q
        if not q or not getattr(self, "e_widget", None):
            return
        if clear:
            answer = None
        elif isinstance(self.e_widget, tk.Text):
            answer = self.e_widget.get("1.0", "end").strip() or None
        else:
            answer = self.e_widget.get() or None
        cur = q.get("answer")
        if not clear:
            if answer == cur or (bank_mod._is_empty(answer) and bank_mod._is_empty(cur)):
                return
        try:
            self.bank = bank_mod.set_answer_local(q["key"], answer, self.bank)
        except Exception as e:
            messagebox.showerror("Not saved", f"The answer could not be cached:\n{e}")
            return
        self.pending_answers[q["key"]] = None if bank_mod._is_empty(answer) else answer
        shown = "(cleared)" if answer is None else str(answer)[:60]
        n_pend = len(self.pending_answers)
        self.e_saved.config(
            text=f"✓ Local ({n_pend} pending)  {datetime.now().strftime('%H:%M:%S')}")
        self.log(f"✓ Local: {(q.get('question') or '')[:70]}  ->  {shown}  "
                 f"({n_pend} pending sync)\n")
        prev = self.qlist.selection()
        self.load_bank_list()
        if prev and self.qlist.exists(prev[0]):
            self.qlist.selection_set(prev[0])
            self.qlist.see(prev[0])
        self.refresh_all()

    def flush_pending_answers(self, reason=""):
        """Push all locally cached answer changes to Supabase in one multi-save."""
        if getattr(self, "_answer_save_after", None):
            try:
                self.after_cancel(self._answer_save_after)
            except Exception:
                pass
            self._answer_save_after = None
            if self.selected_q and getattr(self, "e_widget", None):
                self._answer_ignore = False
                self.save_answer()
        pending = dict(self.pending_answers)
        if not pending:
            return 0
        try:
            bank_mod.flush_answers(pending, self.bank)
        except Exception as e:
            messagebox.showerror(
                "Answers not synced",
                f"{len(pending)} local answer(s) could not be saved to Supabase:\n{e}")
            self.log(f"! Answer sync failed ({len(pending)} pending): {e}\n")
            return 0
        self.pending_answers.clear()
        why = f" ({reason})" if reason else ""
        self.log(f"✓ Synced {len(pending)} answer(s) to Supabase{why}\n")
        if getattr(self, "e_saved", None):
            self.e_saved.config(
                text=f"✓ Synced {len(pending)}  {datetime.now().strftime('%H:%M:%S')}")
        return len(pending)

    def _on_tab_changed(self, _event=None):
        self.flush_pending_answers(reason="tab change")

    def _on_close(self):
        try:
            self.flush_pending_answers(reason="app close")
        except Exception:
            pass
        self.destroy()

    # ------------------------------------------------------------ AI tab (Greenhouse-only settings)

    def _ai_tab(self):
        tab = ttk.Frame(self.tabs, padding=12)
        self.ai_tab = tab
        self.tabs.add(tab, text="  AI  ")
        self.ai_conf = ai_settings.load()
        self.ai_widgets = {}

        ttk.Label(tab, text=f"Greenhouse AI settings → {ai_settings.settings_path().name} "
                            f"(independent of Ashby)",
                  foreground="#666").pack(anchor="w", pady=(0, 8))

        box = ttk.LabelFrame(tab, text="  Resume writing  ", padding=10)
        box.pack(fill="x")
        ttk.Label(box, text="Writes the tailored resume PDF for each Greenhouse job.",
                  foreground="#666", wraplength=900).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Label(box, text="Platform").grid(row=1, column=0, sticky="w")
        plat = ttk.Combobox(box, state="readonly", width=52,
                            values=[f"{k} - {v}" for k, v in ai_settings.PROVIDERS.items()])
        plat.grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=2)
        model_lbl = ttk.Label(box, text="Model")
        model_lbl.grid(row=2, column=0, sticky="w")
        model = ttk.Combobox(box, width=28)
        model.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        test_btn = ttk.Button(box, text="Test", command=lambda: self.test_ai("resume"))
        test_btn.grid(row=2, column=2, sticky="w")
        model_hint = ttk.Label(box, text="Empty model = platform default. Prefer Claude Code or an API key.",
                               foreground="#666")
        model_hint.grid(row=3, column=0, columnspan=3, sticky="w")
        web_hint = ttk.Label(box, text="Web chat (DeepSeek/ChatGPT/Claude.ai) is RISKY for bulk resumes — "
                                       "prefer Claude Code or API. Threads are deleted after each ask.",
                             foreground="#666", wraplength=900)
        test_lbl = ttk.Label(box, text="", foreground="#666", wraplength=900)
        test_lbl.grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))

        provider = self.ai_conf["resume"]["provider"]
        plat.set(f"{provider} - {ai_settings.PROVIDERS.get(provider, '')}")
        model["values"] = ai_settings.MODELS.get(provider, [""])
        model.set(self.ai_conf["resume"]["model"])
        self.ai_widgets["resume"] = (plat, model, test_lbl, model_lbl, test_btn, model_hint, web_hint)
        plat.bind("<<ComboboxSelected>>", lambda e: self._on_ai_platform("resume"))
        self._ai_platform_ui("resume")

        kbox = ttk.LabelFrame(tab, text="  API keys (for claude_api / openai_api)  ", padding=10)
        kbox.pack(fill="x", pady=(10, 0))
        ttk.Label(kbox, text="Saved in Supabase (shared with Resume Builder). Paid per use.",
                  foreground="#666", wraplength=900).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
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
        self.k_status = ttk.Label(kbox, text="loading saved keys …", foreground="#666")
        self.k_status.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.after(200, self.load_keys)

        browser_bar = ttk.Frame(tab)
        browser_bar.pack(fill="x", pady=(10, 0))
        ttk.Button(browser_bar, text="Open bot browser", command=self.open_bot_browser).pack(side="left")
        ttk.Button(browser_bar, text="Open mail browser", command=self.open_mail_browser).pack(
            side="left", padx=(8, 0))
        ttk.Label(browser_bar, foreground="#666", wraplength=700, text=(
            "  Greenhouse Chrome = MyGreenhouse / AI. Mail Chrome = Gmail for "
            "application security codes. Close both before Apply.")).pack(side="left", padx=(8, 0))

        save_row = ttk.Frame(tab)
        save_row.pack(fill="x", pady=(8, 4))
        self.ai_status = ttk.Label(save_row, text="", foreground="#1a7f37")
        self.ai_status.pack(side="left")
        ttk.Button(save_row, text="Save AI settings", command=self.save_ai).pack(side="right")

        pbox = ttk.LabelFrame(tab, text="  Resume JSON prompt  ", padding=10)
        pbox.pack(fill="both", expand=True)
        src = ttk.Frame(pbox)
        src.pack(fill="x")
        self.p_source = tk.StringVar(value=self.ai_conf.get("prompt_source") or "resume_builder")
        ttk.Radiobutton(src, text="Resume Builder prompt (shared with extension)",
                        value="resume_builder", variable=self.p_source,
                        command=self.prompt_source_changed).pack(side="left")
        ttk.Radiobutton(src, text="Local prompt (Greenhouse only)", value="local",
                        variable=self.p_source, command=self.prompt_source_changed).pack(
            side="left", padx=16)
        self.p_text = ScrolledText(pbox, wrap="word", font=("Consolas", 9), height=10, undo=True)
        self.p_text.pack(fill="both", expand=True, pady=6)
        self.p_text.bind("<<Modified>>", self._prompt_modified)
        pb = ttk.Frame(pbox)
        pb.pack(fill="x")
        self.p_info = ttk.Label(pb, text="", foreground="#666")
        self.p_info.pack(side="left")
        ttk.Button(pb, text="Save prompt", command=self.save_prompt).pack(side="right")
        ttk.Button(pb, text="Built-in default", command=lambda: self.load_prompt("default")).pack(
            side="right", padx=6)
        ttk.Button(pb, text="Reload", command=lambda: self.load_prompt()).pack(side="right")
        self._rb_prompt = None
        self.load_prompt()

    def _flush_ai_settings(self):
        """Persist AI tab (including unsaved Platform change) before Make resume / Apply."""
        try:
            if getattr(self, "ai_widgets", None):
                self.save_ai(quiet=True)
        except Exception:
            pass

    def _ai_choice(self, task):
        plat, model = self.ai_widgets[task][0], self.ai_widgets[task][1]
        provider = plat.get().split(" - ")[0]
        if provider in ai_settings.WEB_PROVIDERS:
            return provider, ""
        return provider, model.get().strip()

    def _ai_platform_ui(self, task):
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
            if not model.get() and ai_settings.MODELS.get(provider):
                model.set(ai_settings.MODELS[provider][0])

    def _on_ai_platform(self, task):
        self._ai_platform_ui(task)
        self.save_ai()

    def save_ai(self, quiet=False):
        provider, model = self._ai_choice("resume")
        self.ai_conf["resume"] = {"provider": provider, "model": model}
        # keep relevance key for shared schema compatibility
        self.ai_conf.setdefault("relevance", ai_settings.DEFAULTS["relevance"])
        if getattr(self, "p_source", None):
            self.ai_conf["prompt_source"] = self.p_source.get()
        ai_settings.save(self.ai_conf)
        msg = f"AI settings saved → {ai_settings.settings_path().name}: resume = {provider} {model or '(default)'}"
        if not quiet:
            self.log(msg + "\n")
            self.ai_status.config(text="✓ Saved")
        return msg

    def _key_status(self, keys):
        parts = [f"{'Claude' if p == 'claude_api' else 'OpenAI'}: " +
                 (f"saved (…{v[-4:]})" if v else "not set") for p, v in keys.items()]
        return "   ·   ".join(parts)

    def load_keys(self):
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
                    self.k_status.config(text=f"Could not load keys: {msg}")
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
        provider, model = self._ai_choice(task)
        lbl = self.ai_widgets[task][2]
        if provider not in ai_settings.NO_BROWSER:
            lbl.config(text="Web chat is tested when Make resume runs (opens in Greenhouse Chrome).")
            return
        lbl.config(text=f"Testing {provider} {model or '(default)'} …")

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

    def _prompt_modified(self, _e=None):
        if self.p_text.edit_modified():
            self.p_info.config(text=f"{len(self.p_text.get('1.0', 'end').strip()):,} characters · not saved")

    def _set_prompt_text(self, text, note):
        self.p_text.delete("1.0", "end")
        self.p_text.insert("1.0", text)
        self.p_text.edit_modified(False)
        self.p_info.config(text=f"{len(text):,} characters · {note}")

    def load_prompt(self, which=None):
        source = self.p_source.get()
        if source == "local" and which is None and ai_settings.local_prompt():
            self._set_prompt_text(ai_settings.local_prompt(),
                                  f"local ({ai_settings.local_prompt_path().name})")
            return
        if self._rb_prompt is not None:
            if which == "default":
                self._set_prompt_text(self._rb_prompt["defaultPrompt"], "built-in default — Save to use it")
            else:
                note = "from Resume Builder" if source == "resume_builder" else \
                    "copied from Resume Builder — Save to keep as Greenhouse local prompt"
                self._set_prompt_text(self._rb_prompt["instructions"], note)
            return
        self.p_info.config(text="loading the prompt from Resume Builder …")

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
            self.p_info.config(
                text=f"{len(text):,} characters · saved as {ai_settings.local_prompt_path().name}")
            self.log(f"Resume prompt saved ({ai_settings.local_prompt_path().name}).\n")
            return
        if not messagebox.askyesno("Save to Resume Builder", (
                "This changes the resume prompt in Resume Builder (Supabase), so the Chrome "
                "extension and the web app will use it too.\n\nSave?")):
            return
        self.p_info.config(text="saving to Resume Builder …")

        def work():
            from resume import save_prompt_to_resume_builder
            try:
                save_prompt_to_resume_builder(text)
                if self._rb_prompt:
                    self._rb_prompt["instructions"] = text
                msg = f"{len(text):,} characters · saved to Resume Builder"
                self.ui_q.put(lambda: (
                    self.p_text.edit_modified(False),
                    self.log("Resume prompt saved to Resume Builder.\n")))
            except Exception as e:
                msg = f"save failed: {str(e)[:150]}"
            self.ui_q.put(lambda: self.p_info.config(text=msg))
        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
