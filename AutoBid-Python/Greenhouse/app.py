"""AutoBid · Greenhouse desktop app.

    python app.py   (or AutoBid-Greenhouse.bat)

Flow:
  1. Collect jobs from companies.csv (public Greenhouse boards API — no login)
  2. Fill Answers / My profile
  3. Make resume (optional) → Apply in bot Chrome (apply link per job)
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBid · Greenhouse")
        self.geometry("1180x780")
        self.minsize(960, 600)
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
        self._checked = set()
        self.current_id = None
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

        self.refresh()
        self._auto_info()
        self.after(150, self._drain)
        self.after(10_000, self._auto_tick)  # first auto-collect check 10 s after start
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _style(self):
        s = ttk.Style(self)
        if "vista" in s.theme_names():
            s.theme_use("vista")

    def _header(self):
        """Title row + Ashby-style orange run bar (status + Stop after / Pause / Stop now)."""
        top = ttk.Frame(self, padding=(12, 10, 12, 6))
        top.pack(fill="x")
        ttk.Label(top, text="AutoBid · Greenhouse", font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Label(top, text="  my.greenhouse.io · Remote", foreground="#666").pack(side="left")
        ttk.Button(top, text="Open bot browser", command=self.open_bot_browser).pack(
            side="left", padx=(16, 0))
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
        self.f_rel = ttk.Combobox(bar, width=12, state="readonly",
                                  values=["relevant", "all", "not relevant"])
        self.f_rel.set("relevant")
        self.f_rel.pack(side="left", padx=4)
        self.f_rel.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Status").pack(side="left", padx=(10, 0))
        self.f_status = ttk.Combobox(bar, width=12, state="readonly",
                                     values=["all"] + list(db.STATUSES))
        self.f_status.set("all")
        self.f_status.pack(side="left", padx=4)
        self.f_status.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Label(bar, text="Posted").pack(side="left", padx=(10, 0))
        self.f_posted = ttk.Combobox(bar, width=10, state="readonly",
                                     values=["all", "today"])
        self.f_posted.set("all")
        self.f_posted.pack(side="left", padx=4)
        self.f_posted.bind("<<ComboboxSelected>>", lambda e: self.load_jobs())
        ttk.Button(bar, text="Today's jobs", command=self.show_todays_jobs).pack(
            side="left", padx=(0, 8))
        self.job_count = ttk.Label(bar, text="", foreground="#666")
        self.job_count.pack(side="left", padx=8)
        ttk.Button(bar, text="Collect jobs", command=self.collect).pack(side="right")
        ttk.Button(bar, text="Collect today's jobs", command=self.collect_today).pack(
            side="right", padx=4)
        ttk.Button(bar, text="Collect (limit 100)",
                   command=lambda: self.run_script(
                       ["collect.py", "--limit", "100", "--no-ai"],
                       "Collect (100)")).pack(side="right", padx=4)

        # Button bar first (bottom) so the list always keeps scrollbars
        btns = ttk.Frame(tab)
        btns.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(btns, text="Select all shown", command=self.select_all).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear_sel).pack(side="left", padx=4)
        ttk.Button(btns, text="Open job", command=self.open_job).pack(side="left", padx=4)
        ttk.Button(btns, text="Open Apply link", command=self.open_apply).pack(side="left", padx=4)
        ttk.Label(btns, text="Click column headers to sort", foreground="#888").pack(
            side="left", padx=12)
        ttk.Button(btns, text="Make resume", command=self.make_resume_selected).pack(side="right", padx=4)
        ttk.Button(btns, text="Dry run", command=lambda: self.apply(True)).pack(side="right", padx=4)
        ttk.Button(btns, text="Apply", command=lambda: self.apply(False)).pack(side="right")

        listf = ttk.Frame(tab)
        listf.pack(fill="both", expand=True)
        cols = ("chk", "status", "company", "title", "posted", "pay", "apply", "resume")
        self.jobs = ttk.Treeview(listf, columns=cols, show="headings", selectmode="extended")
        self.job_headings = {}
        self.sort_col, self.sort_desc = "posted", True
        self.jobs.heading("chk", text="☐", command=self.toggle_all_shown)
        self.jobs.column("chk", width=34, minwidth=34, anchor="center", stretch=False)
        for c, t, w in [("status", "Status", 80), ("company", "Company", 130),
                        ("title", "Title", 280), ("posted", "Posted", 110),
                        ("pay", "Pay", 120), ("apply", "Apply", 55), ("resume", "Resume", 55)]:
            self.job_headings[c] = t
            self.jobs.heading(c, text=t + (" ⇅" if c != self.sort_col else ""),
                              command=lambda c=c: self.sort_jobs(c, toggle=True))
            self.jobs.column(c, width=w, stretch=c == "title")
        vsb = ttk.Scrollbar(listf, orient="vertical", command=self.jobs.yview)
        hsb = ttk.Scrollbar(listf, orient="horizontal", command=self.jobs.xview)
        self.jobs.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.jobs.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        listf.rowconfigure(0, weight=1)
        listf.columnconfigure(0, weight=1)
        self.jobs.bind("<Button-1>", self._on_click)
        self.jobs.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        self._update_job_headings()

    def _answers_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="  Answers  ")
        split = ttk.PanedWindow(tab, orient="horizontal")
        split.pack(fill="both", expand=True)

        box = ttk.LabelFrame(split, text="  My profile (Greenhouse)  ", padding=10)
        self.p_entries = {}
        fields = [
            ("name", "Full Name"), ("first_name", "First Name"), ("last_name", "Last Name"),
            ("email", "Email"), ("phone", "Phone"), ("location", "Location"),
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
        ttk.Button(box, text="Save profile", command=self.save_profile).grid(
            row=len(fields), column=1, sticky="e", pady=8)
        ttk.Label(box, foreground="#666", wraplength=320, text=(
            "Profile is shared via Supabase keys; answer bank rows use platform=greenhouse "
            "(independent of Ashby)."
        )).grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        split.add(box, weight=1)

        right = ttk.Frame(split, padding=(10, 0, 0, 0))
        ttk.Label(right, text="Required questions (Greenhouse bank)",
                  font=("Segoe UI Semibold", 10)).pack(anchor="w")
        sortbar = ttk.Frame(right)
        sortbar.pack(fill="x", pady=(4, 0))
        ttk.Label(sortbar, text="Sort by", foreground="#666").pack(side="left")
        self.q_sort_pick = ttk.Combobox(
            sortbar, width=14, state="readonly",
            values=["Required in", "Type", "Question", "Answer"])
        self.q_sort_pick.set("Required in")
        self.q_sort_pick.pack(side="left", padx=4)
        self.q_sort_pick.bind("<<ComboboxSelected>>", lambda e: self._q_sort_from_picker())
        self.q_sort_dir_btn = ttk.Button(sortbar, text="▼ High→Low", width=12,
                                         command=self._q_toggle_sort_dir)
        self.q_sort_dir_btn.pack(side="left", padx=4)
        ttk.Label(sortbar, text="Search", foreground="#666").pack(side="left", padx=(16, 0))
        self.q_search = ttk.Entry(sortbar, width=28)
        self.q_search.pack(side="left", padx=4)
        self.q_search.bind("<KeyRelease>", self._on_q_search)
        self.q_search.bind("<Escape>", lambda e: self._clear_q_search())
        self._q_search_after = None
        self.q_only_open = tk.BooleanVar(value=False)
        ttk.Checkbutton(sortbar, text="only unanswered", variable=self.q_only_open,
                        command=self.load_bank_list).pack(side="right")
        self.q_count = ttk.Label(right, text="", foreground="#666")
        self.q_count.pack(anchor="w")

        qframe = ttk.Frame(right)
        cols = ("jobs", "type", "question", "answer")
        self.qlist = ttk.Treeview(qframe, columns=cols, show="headings", selectmode="browse",
                                  height=14)
        self.q_headings = {
            "jobs": "Required in", "type": "Type", "question": "Question", "answer": "Answer"}
        self.q_sort_col, self.q_sort_desc = "jobs", True
        for c, label, w in [("jobs", "Required in", 90), ("type", "Type", 100),
                            ("question", "Question", 360), ("answer", "Answer", 180)]:
            self.qlist.heading(c, text=label,
                               command=lambda c=c: self.sort_questions(c, toggle=True))
            self.qlist.column(c, width=w, stretch=c == "question")
        self.qlist.tag_configure("open", foreground="#c62828")
        qsb = ttk.Scrollbar(qframe, orient="vertical", command=self.qlist.yview)
        self.qlist.configure(yscrollcommand=qsb.set)
        self.qlist.grid(row=0, column=0, sticky="nsew")
        qsb.grid(row=0, column=1, sticky="ns")
        qframe.rowconfigure(0, weight=1)
        qframe.columnconfigure(0, weight=1)
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
        self.e_question = ttk.Label(ed, text="Select a question above.", wraplength=700,
                                    justify="left")
        self.e_question.pack(anchor="w")
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
            "1) Collect jobs from companies.csv (public Greenhouse boards API — no login)\n"
            "2) AI tab → resume platform (Claude Code / API / …) — independent of Ashby\n"
            "3) Fill Answers / My profile → Select jobs → Make resume → Apply\n"
            "4) Apply opens each job’s apply link in bot Chrome (sign in only if a board asks)\n"
            "5) Apply uploads the tailored PDF and submits"
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

    def _job_is_today(self, row):
        """Posted label looks like today, or first seen today (UTC date)."""
        try:
            from greenhouse_browser import posted_looks_like_today
            if posted_looks_like_today(row["published_at"] or ""):
                return True
        except Exception:
            pass
        # first_seen_at ISO date == today
        fs = (row["first_seen_at"] or "")[:10]
        from datetime import date
        return fs == date.today().isoformat()

    def load_jobs(self):
        sql = "SELECT * FROM jobs WHERE is_active=1"
        if self.f_rel.get() == "relevant":
            sql += " AND relevant=1"
        elif self.f_rel.get() == "not relevant":
            sql += " AND relevant=0"
        st = self.f_status.get() if getattr(self, "f_status", None) else "all"
        if st and st != "all":
            sql += " AND status=?"
            rows = self.conn.execute(sql + " ORDER BY COALESCE(published_at, first_seen_at) DESC",
                                     (st,)).fetchall()
        else:
            rows = self.conn.execute(
                sql + " ORDER BY COALESCE(published_at, first_seen_at) DESC").fetchall()
        if self.f_posted.get() == "today":
            rows = [r for r in rows if self._job_is_today(r)]
        keep = set(self.jobs.selection())
        self.jobs.delete(*self.jobs.get_children())
        for r in rows:
            posted = format_posted(r["published_at"]) or (r["first_seen_at"] or "")[:10]
            has_resume = bool(r["resume_path"] and Path(r["resume_path"]).is_file())
            self.jobs.insert("", "end", iid=r["id"], values=(
                "☑" if r["id"] in self._checked else "☐",
                r["status"], r["company"] or r["org"], r["title"],
                posted, r["compensation"] or "",
                "yes" if r["apply_url"] else "—",
                "✓" if has_resume else "—"))
        for i in keep:
            if self.jobs.exists(i):
                self.jobs.selection_add(i)
        if self.sort_col:
            self.sort_jobs(self.sort_col, toggle=False)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1").fetchone()["c"]
        ready = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 AND status='ready'"
        ).fetchone()["c"]
        applied = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND status='applied'"
        ).fetchone()["c"]
        shown = len(rows)
        today_bit = f" · {shown} today" if self.f_posted.get() == "today" else f" · {shown} shown"
        self.stats.config(
            text=f"{n} relevant · {ready} ready · {applied} applied · "
                 f"{len(self._checked)} selected{today_bit}")
        if getattr(self, "job_count", None):
            self.job_count.config(text=f"{shown} in list")

    def _update_job_headings(self):
        for c, label in self.job_headings.items():
            if self.sort_col == c:
                arrow = " ▼" if self.sort_desc else " ▲"
            else:
                arrow = " ⇅"
            self.jobs.heading(c, text=label + arrow)

    def sort_jobs(self, col, toggle=False):
        """Click a Jobs column header to sort; click again to reverse."""
        if toggle:
            self.sort_desc = (not self.sort_desc) if self.sort_col == col else False
            self.sort_col = col
        if col == "pay":
            def key(i):
                v = self.jobs.set(i, col) or ""
                import re as _re
                m = _re.search(r"(\d[\d,.]*)", v)
                return float(m.group(1).replace(",", "")) if m else -1
        else:
            def key(i):
                return (self.jobs.set(i, col) or "").lower()
        rows = [(key(i), i) for i in self.jobs.get_children()]
        rows.sort(reverse=self.sort_desc)
        for n, (_, iid) in enumerate(rows):
            self.jobs.move(iid, "", n)
        self._update_job_headings()

    def toggle_all_shown(self):
        rows = self.jobs.get_children()
        if rows and all(i in self._checked for i in rows):
            self._checked.clear()
        else:
            self._checked.update(rows)
        self.load_jobs()

    def show_todays_jobs(self):
        self.f_posted.set("today")
        self.f_rel.set("relevant")
        if getattr(self, "f_status", None):
            self.f_status.set("all")
        self.load_jobs()
        self.tabs.select(0)

    def collect_today(self, auto=False):
        return self._start_or_queue_collect("today", auto=auto)

    def collect(self, auto=False):
        return self._start_or_queue_collect("all", auto=auto)

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
        try:
            bank_mod.sync_bank(self.conn, self.bank)
            bank_mod.save_bank(self.bank)
        except Exception:
            pass
        self.load_jobs()
        if getattr(self, "qlist", None):
            try:
                self.load_bank_list()
            except Exception:
                pass

    def _on_click(self, event):
        row = self.jobs.identify_row(event.y)
        col = self.jobs.identify_column(event.x)
        if not row:
            return
        self.current_id = row
        if col == "#1":
            if row in self._checked:
                self._checked.discard(row)
            else:
                self._checked.add(row)
            vals = list(self.jobs.item(row, "values"))
            vals[0] = "☑" if row in self._checked else "☐"
            self.jobs.item(row, values=vals)
            # refresh only the selected count in the header
            cur = self.stats.cget("text")
            parts = [p.strip() for p in cur.split("·")]
            if parts:
                parts[-1] = f"{len(self._checked)} selected" if "selected" in parts[-1] else parts[-1]
                # keep last "shown/today" bit if present
                shown = next((p for p in parts if "shown" in p or "today" in p), None)
                base = [p for p in parts if "selected" not in p and "shown" not in p and "today" not in p]
                base.append(f"{len(self._checked)} selected")
                if shown:
                    base.append(shown)
                self.stats.config(text=" · ".join(base))

    def select_all(self):
        for i in self.jobs.get_children():
            self._checked.add(i)
        self.load_jobs()

    def clear_sel(self):
        self._checked.clear()
        self.load_jobs()

    def selected_ids(self):
        return [i for i in self.jobs.get_children() if i in self._checked] or (
            [self.current_id] if self.current_id else [])

    def open_job(self):
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
        row = self.conn.execute("SELECT apply_url FROM jobs WHERE id=?", (ids[0],)).fetchone()
        if row and row["apply_url"]:
            webbrowser.open(row["apply_url"])
        else:
            messagebox.showinfo("Apply link", "No Apply link remembered for this job yet.")

    def save_profile(self):
        vals = {k: e.get().strip() for k, e in self.p_entries.items()}
        self.bank = bank_mod.set_profile(vals, self.bank)
        messagebox.showinfo("Saved", "Profile saved to Supabase.")

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
                "Apply", f"Submit applications to {len(ids)} Greenhouse job(s)?"):
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
        self.run_label.config(text=f"●  Running: {label}", bg="#fff4e5", fg="#7a4a00")
        self.run_bar.config(bg="#fff4e5")
        self.b_stop_after.config(text="Stop after this job", state="normal")
        self._set_pause_button(paused=False)
        self.b_pause.config(state="normal")
        self.run_bar.pack(fill="x", padx=10, pady=(0, 6), after=self._header_frame)

    def _hide_run_bar(self):
        PAUSE_FLAG.unlink(missing_ok=True)
        self._set_pause_button(paused=False)
        self.run_bar.pack_forget()

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

    def _ensure_chrome_free(self, auto=False):
        """Apply/Collect need exclusive use of .browser-profile-greenhouse."""
        try:
            from browser import close_bot_chrome, get_profile_dir, profile_busy
        except Exception:
            return True
        if not profile_busy():
            return True
        profile = get_profile_dir()
        if auto:
            # Scheduled collect: close without a dialog so the app does not freeze overnight
            n = close_bot_chrome()
            self.log(f"(auto) Closed Greenhouse Chrome ({n} process(es)) → {profile.name}\n")
            import time as _t
            _t.sleep(0.8)
            return not profile_busy()
        if not messagebox.askyesno(
                "Greenhouse Chrome is open",
                f"“Open bot browser” (or another Greenhouse task) is using:\n{profile}\n\n"
                "Close that Chrome now so Apply / Collect can run?\n\n"
                "(Ashby’s Chrome is separate and will not be closed.)"):
            self.log("Cancelled — close Greenhouse Chrome, then try again.\n")
            return False
        n = close_bot_chrome()
        self.log(f"Closed Greenhouse Chrome ({n} process(es)) → {profile.name}\n")
        import time as _t
        _t.sleep(0.8)
        return True

    def run_script(self, args, label, quiet=False):
        if self.proc and self.proc.poll() is None:
            if not quiet:
                messagebox.showwarning("Busy", "Something is already running. Stop it first.")
            return False
        # Apply / Make resume need exclusive Greenhouse Chrome (Collect is API-only)
        if args and Path(str(args[0])).name in ("bid.py", "make_resume.py"):
            if not self._ensure_chrome_free(auto=quiet):
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
                    self.refresh()
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
        needle = (self.q_search.get() if getattr(self, "q_search", None) else "") or ""
        needle = needle.strip().lower()
        self.qlist.delete(*self.qlist.get_children())
        rows = []
        open_n = 0
        for i, q in enumerate(self.bank.get("questions") or []):
            if not q.get("asked_in_jobs"):
                continue
            empty = bank_mod._is_empty(q.get("answer"))
            ans = "" if empty else (
                ", ".join(q["answer"]) if isinstance(q["answer"], list) else str(q["answer"]))
            if needle:
                blob = f"{q.get('question') or ''} {ans} {q.get('type') or ''}".lower()
                if needle not in blob:
                    continue
            open_n += empty
            if self.q_only_open.get() and not empty:
                continue
            n = int(q.get("asked_in_jobs") or 0)
            rows.append({
                "iid": str(i),
                "tags": ("open",) if empty else (),
                "values": (f"{n} job{'s' if n != 1 else ''}", q.get("type") or "",
                           q.get("question") or "", (ans or "")[:80]),
                "jobs_n": n,
                "type": q.get("type") or "",
                "question": q.get("question") or "",
                "answer": ans or "",
                "empty": empty,
            })
        if self.q_sort_col:
            rows.sort(key=self._q_sort_key, reverse=self.q_sort_desc)
        for r in rows:
            self.qlist.insert("", "end", iid=r["iid"], tags=r["tags"], values=r["values"])
        self._update_q_headings()
        search_bit = f"  ·  search “{needle}”" if needle else ""
        self.q_count.config(
            text=f"{len(rows)} questions shown  ·  {open_n} unanswered{search_bit}  ·  "
                 f"platform=greenhouse")

    def edit_question(self):
        sel = self.qlist.selection()
        if not sel:
            return
        q = self.bank["questions"][int(sel[0])]
        self.selected_q = q
        self.e_question.config(text=q.get("question") or "")
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
        self.load_jobs()

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
        ttk.Label(browser_bar, foreground="#666", wraplength=780, text=(
            "  Greenhouse Chrome (.browser-profile-greenhouse) for AI web logins + MyGreenhouse. "
            "Close it before Make resume / Apply.")).pack(side="left", padx=(8, 0))

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
