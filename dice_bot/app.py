"""Dice Auto Bid: the window. Run: python app.py (or start.bat)."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot import config, engine  # noqa: E402
from bot.store import ledger, ledger_applied_today  # noqa: E402
from bot.worker import Worker  # noqa: E402

RED, RED_DARK, INK, MUTED, BG, CARD, LINE = "#cc1f2f", "#a3141f", "#1d2433", "#6b7385", "#f4f5f8", "#ffffff", "#dde1ea"
ENGINES = {"deepseek_chat": "DeepSeek chat (browser, no API cost)", "deepseek_api": "DeepSeek API", "openai": "OpenAI API"}
LEVEL_COLORS = {"ok": "#15803d", "warn": "#b45309", "err": "#b91c1c", "dim": "#8a90a0", "info": INK}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{config.APP_NAME} {config.VERSION}")
        self.geometry("1080x760")
        self.minsize(900, 620)
        self.configure(bg=BG)
        try:
            self.iconbitmap(str(config.ASSETS / "icon.ico"))
            self._icon = tk.PhotoImage(file=str(config.ASSETS / "icon.png"))
            self.iconphoto(True, self._icon)
        except Exception:
            self._icon = None
        self.s = config.load()
        self.worker = Worker()
        self.worker.start()
        self.rows: dict[str, str] = {}
        self._style()
        self._header()
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.tab_run = ttk.Frame(nb, style="Card.TFrame", padding=12)
        self.tab_set = ttk.Frame(nb, style="Card.TFrame", padding=12)
        self.tab_man = ttk.Frame(nb, style="Card.TFrame", padding=12)
        self.tab_his = ttk.Frame(nb, style="Card.TFrame", padding=12)
        nb.add(self.tab_run, text="  Run  ")
        nb.add(self.tab_set, text="  Settings  ")
        nb.add(self.tab_man, text="  Manual bid  ")
        nb.add(self.tab_his, text="  History  ")
        nb.bind("<<NotebookTabChanged>>", lambda e: self._history() if nb.index("current") == 3 else None)
        self._run_tab()
        self._settings_tab()
        self._manual_tab()
        self._history_tab()
        self._update_count(ledger_applied_today(), self.s["daily_limit"])
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(150, self._poll)
        if not self.s.get("profile_path"):
            self.log("Start here: Settings → choose your resume / profile JSON, then Open browser and sign in to Dice.", "warn")

    # ---------- look ----------
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=INK, font=("Segoe UI", 10))
        st.configure("Card.TFrame", background=CARD)
        st.configure("TFrame", background=CARD)
        st.configure("TLabel", background=CARD, foreground=INK)
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=("Segoe UI", 9))
        st.configure("H.TLabel", background=CARD, foreground=INK, font=("Segoe UI Semibold", 11))
        st.configure("TCheckbutton", background=CARD)
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 7), font=("Segoe UI Semibold", 10), background="#e7e9ef", foreground=MUTED)
        st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", RED)])
        st.configure("Accent.TButton", background=RED, foreground="white", font=("Segoe UI Semibold", 10), padding=(16, 8), borderwidth=0)
        st.map("Accent.TButton", background=[("active", RED_DARK), ("disabled", "#e3a1a7")])
        st.configure("TButton", padding=(12, 7), background="#eef0f4", borderwidth=1)
        st.configure("Treeview", rowheight=26, fieldbackground=CARD, background=CARD, bordercolor=LINE)
        st.configure("Treeview.Heading", font=("Segoe UI Semibold", 9), background="#eef0f4")
        st.configure("TEntry", fieldbackground="white", padding=5)
        st.configure("TCombobox", padding=4)
        st.configure("TLabelframe", background=CARD, bordercolor=LINE)
        st.configure("TLabelframe.Label", background=CARD, foreground=RED, font=("Segoe UI Semibold", 10))

    def _header(self):
        h = tk.Frame(self, bg=BG)
        h.pack(fill="x", padx=14, pady=12)
        if self._icon:
            small = self._icon.subsample(max(1, self._icon.width() // 40))
            self._icon_small = small
            tk.Label(h, image=small, bg=BG).pack(side="left", padx=(0, 10))
        box = tk.Frame(h, bg=BG)
        box.pack(side="left")
        tk.Label(box, text=config.APP_NAME, font=("Segoe UI Semibold", 17), fg=INK, bg=BG).pack(anchor="w")
        tk.Label(box, text="Recommended remote jobs · ATS-tailored resume · Easy Apply", font=("Segoe UI", 9), fg=MUTED, bg=BG).pack(anchor="w")
        right = tk.Frame(h, bg=BG)
        right.pack(side="right")
        self.lbl_count = tk.Label(right, text="Today 0 / 0", font=("Segoe UI Semibold", 12), fg=RED, bg=BG)
        self.lbl_count.pack(anchor="e")
        self.lbl_status = tk.Label(right, text="Idle", font=("Segoe UI", 9), fg=MUTED, bg=BG)
        self.lbl_status.pack(anchor="e")

    # ---------- run ----------
    def _run_tab(self):
        t = self.tab_run
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        self.btn_start = ttk.Button(bar, text="▶  Start", style="Accent.TButton", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(bar, text="■  Stop", command=self._stop, state="disabled")
        self.btn_stop.pack(side="left", padx=8)
        ttk.Button(bar, text="Open browser", command=lambda: self._cmd("open_browser")).pack(side="left")
        ttk.Button(bar, text="Open bids folder", command=lambda: self._open_path(self.s["output_dir"])).pack(side="left", padx=8)
        self.var_dry = tk.BooleanVar(value=bool(self.s.get("dry_run")))
        ttk.Checkbutton(bar, text="Dry run (do not press Submit)", variable=self.var_dry, command=self._save_dry).pack(side="left", padx=10)

        cols = ("time", "company", "title", "status", "ats", "risk")
        self.tree = ttk.Treeview(t, columns=cols, show="headings", height=9)
        for c, w in zip(cols, (60, 200, 360, 110, 60, 60)):
            self.tree.heading(c, text=c.upper() if c in ("ats",) else c.capitalize())
            self.tree.column(c, width=w, anchor="w" if c in ("company", "title") else "center")
        self.tree.pack(fill="x", pady=(12, 8))
        self.tree.bind("<Double-1>", lambda e: self._open_selected(self.tree, self.rows_url))
        self.rows_url: dict[str, str] = {}
        self.tree.tag_configure("applied", foreground="#15803d")
        self.tree.tag_configure("needs you", foreground="#b45309")
        self.tree.tag_configure("failed", foreground="#b91c1c")
        self.tree.tag_configure("skipped", foreground="#8a90a0")
        ttk.Label(t, text="Log", style="H.TLabel").pack(anchor="w")
        self.txt = ScrolledText(t, height=14, font=("Consolas", 9), relief="flat", bg="#fbfbfd", bd=1, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=(4, 0))
        for k, c in LEVEL_COLORS.items():
            self.txt.tag_configure(k, foreground=c)

    def log(self, text, level="info"):
        self.txt.insert("end", f"{datetime.now():%H:%M:%S}  {text}\n", level)
        self.txt.see("end")

    def _start(self):
        self._collect_settings(quiet=True)
        if not self.s.get("profile_path"):
            return messagebox.showwarning(config.APP_NAME, "Choose your resume / profile JSON in Settings first.")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self._cmd("start")

    def _stop(self):
        self.worker.stop.set()
        self.lbl_status.configure(text="Stopping after the current step…")

    def _save_dry(self):
        self.s["dry_run"] = self.var_dry.get()
        config.save(self.s)

    def _cmd(self, name, *args):
        if self.worker.busy and name != "quit":
            return messagebox.showinfo(config.APP_NAME, "The bot is busy. Stop it first or wait.")
        self.worker.cmd.put((name, dict(self.s), *args))

    def _update_count(self, n, limit):
        self.lbl_count.configure(text=f"Today {n} / {limit}")

    def _poll(self):
        try:
            while True:
                ev = self.worker.events.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self.log(ev[1], ev[2])
                elif kind == "status":
                    self.lbl_status.configure(text=ev[1])
                elif kind == "count":
                    self._update_count(ev[1], ev[2])
                elif kind == "job":
                    r = ev[1]
                    vals = (r["time"], r["company"], r["title"], r["status"], r["ats"], r["risk"])
                    iid = r["url"]
                    if self.tree.exists(iid):
                        self.tree.item(iid, values=vals, tags=(r["status"],))
                    else:
                        self.tree.insert("", 0, iid=iid, values=vals, tags=(r["status"],))
                    self.rows_url[iid] = r["url"]
                elif kind == "idle":
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                elif kind == "job_read":
                    self._fill_manual(ev[1])
                elif kind == "manual_saved":
                    messagebox.showinfo(config.APP_NAME, f"Saved to Supabase (bid #{ev[1]}).")
                    self._history()
        except queue.Empty:
            pass
        self.after(150, self._poll)

    # ---------- settings ----------
    def _settings_tab(self):
        t = self.tab_set
        canvas = tk.Canvas(t, bg=CARD, highlightthickness=0)
        sb = ttk.Scrollbar(t, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units") if self.tab_set.winfo_ismapped() else None)
        self.vars: dict[str, tk.Variable] = {}

        def group(title):
            g = ttk.LabelFrame(inner, text=title, padding=10)
            g.pack(fill="x", padx=4, pady=6)
            g.columnconfigure(1, weight=1)
            return g

        def row(g, r, key, label, kind="entry", hint="", **kw):
            ttk.Label(g, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=4)
            val = self.s.get(key)
            if kind == "check":
                v = tk.BooleanVar(value=bool(val))
                ttk.Checkbutton(g, variable=v).grid(row=r, column=1, sticky="w")
            elif kind == "combo":
                v = tk.StringVar(value=kw["labels"].get(val, val))
                ttk.Combobox(g, textvariable=v, values=list(kw["labels"].values()), state="readonly", width=40).grid(row=r, column=1, sticky="w")
                v._labels = kw["labels"]
            elif kind == "spin":
                v = tk.StringVar(value=str(val))
                ttk.Spinbox(g, textvariable=v, from_=kw.get("lo", 0), to=kw.get("hi", 999), width=8).grid(row=r, column=1, sticky="w")
            else:
                v = tk.StringVar(value=str(val or ""))
                e = ttk.Entry(g, textvariable=v, show="•" if kind == "secret" else "")
                e.grid(row=r, column=1, sticky="ew")
                if kind in ("file", "dir"):
                    ttk.Button(g, text="Browse…", command=lambda v=v, k=kind: self._browse(v, k)).grid(row=r, column=2, padx=(6, 0))
            if hint:
                ttk.Label(g, text=hint, style="Muted.TLabel", wraplength=330).grid(row=r, column=3, sticky="w", padx=8)
            self.vars[key] = v

        g = group("Resume")
        row(g, 0, "profile_path", "Resume / profile JSON", "file")
        self.lbl_profile = ttk.Label(g, text="", style="Muted.TLabel", wraplength=560)
        self.lbl_profile.grid(row=1, column=1, columnspan=3, sticky="w")
        row(g, 2, "output_dir", "Save bids in", "dir")
        try:
            tpls = {t["id"]: t["name"] for t in engine.call("templates")}
        except Exception:
            tpls = {"professional": "Professional"}
        row(g, 3, "template", "Word style (font, colour)", "combo", labels=tpls)
        row(g, 4, "cover_letter", "Write a cover letter", "check")
        row(g, 5, "ats_target", "ATS target %", "spin", lo=50, hi=100)
        row(g, 6, "fix_rounds", "Correction rounds", "spin", "when ATS is under target or the consistency check finds problems", lo=0, hi=3)

        g = group("AI")
        row(g, 0, "engine", "Resume writer", "combo", labels=ENGINES)
        row(g, 1, "openai_key", "OpenAI API key", "secret")
        row(g, 2, "openai_model", "OpenAI model")
        row(g, 3, "deepseek_key", "DeepSeek API key", "secret")
        row(g, 4, "deepseek_model", "DeepSeek model", hint="deepseek-chat or deepseek-reasoner")
        row(g, 5, "keep_chat_tab", "Keep the DeepSeek tab open", "check")

        g = group("Jobs and account safety")
        row(g, 0, "max_age_days", "Posted within (days)", "spin", "1 = today (24 h), 2 = today + yesterday, …", lo=1, hi=30)
        row(g, 1, "daily_limit", "Daily limit", "spin", "Dice applications per day, bot + manual", lo=1, hi=200)
        row(g, 2, "run_limit", "Limit per Start", "spin", lo=1, hi=200)
        row(g, 3, "delay_min", "Pause between jobs, min (s)", "spin", lo=0, hi=3600)
        row(g, 4, "delay_max", "Pause between jobs, max (s)", "spin", lo=0, hi=3600)
        row(g, 5, "max_pages", "Recommended pages to read", "spin", lo=1, hi=50)
        row(g, 6, "strict_remote", "Strict remote", "check", "also reject jobs tagged On-site / Hybrid when the location says Remote")

        g = group("Supabase and browser")
        row(g, 0, "supabase_url", "Supabase URL", hint="from the repo's .env by default")
        row(g, 1, "supabase_key", "Supabase secret key", "secret")
        row(g, 2, "chrome_channel", "Browser", "combo", labels={"attach": "My Chrome window (attach, recommended)", "chrome": "Google Chrome (bot's own window)",
                                                                  "msedge": "Microsoft Edge (bot's own window)", "chromium": "Chromium (downloaded by the bot)"})
        row(g, 3, "cdp_port", "Chrome debug port", "spin", "attach mode: the port of the Chrome window (start_chrome.bat uses 9222)", lo=1024, hi=65000)

        ttk.Button(inner, text="Save settings", style="Accent.TButton", command=self._collect_settings).pack(anchor="w", padx=4, pady=10)
        self._show_profile()

    def _browse(self, var, kind):
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=var.get() or os.path.expanduser("~"))
        else:
            p = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            var.set(p)
            if kind == "file":
                self._collect_settings(quiet=True)
                self._show_profile()

    def _show_profile(self):
        p = self.s.get("profile_path")
        if not p:
            self.lbl_profile.configure(text="Not chosen yet: a full profile (like extension-v2/profiles/sample-profile.json) or a JSON Resume.")
            return
        try:
            prof = engine.load_profile(p)
            jobs = len(prof["resume"].get("work", []))
            self.lbl_profile.configure(text=f"✓ {prof['name']} · {prof['resume']['basics'].get('label', '')} · {jobs} jobs · {prof['totalYears']} years · files: "
                                            f"{prof['resume']['basics'].get('name', 'Name').replace(' ', '_')}_cv.docx")
        except Exception as e:
            self.lbl_profile.configure(text=f"✗ {e}")

    def _collect_settings(self, quiet=False):
        for k, v in self.vars.items():
            val = v.get()
            if hasattr(v, "_labels"):
                val = next((key for key, lab in v._labels.items() if lab == val), val)
            if isinstance(self.s.get(k), bool):
                val = bool(val)
            elif isinstance(config.DEFAULTS.get(k), int) and not isinstance(config.DEFAULTS.get(k), bool):
                try:
                    val = int(val)
                except ValueError:
                    val = config.DEFAULTS[k]
            self.s[k] = val
        self.s["dry_run"] = self.var_dry.get()
        config.save(self.s)
        self._update_count(ledger_applied_today(), self.s["daily_limit"])
        if not quiet:
            self._show_profile()
            self.log("Settings saved.", "ok")

    # ---------- manual bid ----------
    def _manual_tab(self):
        t = self.tab_man
        t.columnconfigure(1, weight=1)
        ttk.Label(t, text="A Dice job you applied to by hand: save it to Supabase like the bot's bids.", style="Muted.TLabel").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self.m: dict[str, tk.StringVar] = {}
        fields = [("url", "Job link"), ("company", "Company"), ("title", "Job title"), ("location", "Location"), ("salary", "Salary"),
                  ("folder", "Resume folder"), ("resume", "Resume file"), ("cover", "Cover letter file"), ("notes", "Notes")]
        for i, (k, label) in enumerate(fields, start=1):
            ttk.Label(t, text=label).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 10))
            v = tk.StringVar()
            ttk.Entry(t, textvariable=v).grid(row=i, column=1, sticky="ew")
            self.m[k] = v
            if k == "url":
                ttk.Button(t, text="Read from Dice", command=self._read_manual).grid(row=i, column=2, padx=6)
            if k in ("folder", "resume", "cover"):
                ttk.Button(t, text="Browse…", command=lambda v=v, k=k: v.set((filedialog.askdirectory() if k == "folder" else filedialog.askopenfilename()) or v.get())).grid(row=i, column=2, padx=6)
        ttk.Label(t, text="Job description").grid(row=len(fields) + 1, column=0, sticky="nw", pady=4)
        self.m_jd = ScrolledText(t, height=9, font=("Segoe UI", 9), relief="flat", bd=1, wrap="word")
        self.m_jd.grid(row=len(fields) + 1, column=1, sticky="nsew", pady=4)
        t.rowconfigure(len(fields) + 1, weight=1)
        ttk.Button(t, text="Save to Supabase", style="Accent.TButton", command=self._save_manual).grid(row=len(fields) + 2, column=1, sticky="w", pady=8)

    def _read_manual(self):
        url = self.m["url"].get().strip()
        if "dice.com" not in url:
            return messagebox.showwarning(config.APP_NAME, "Paste a dice.com job link first.")
        self._cmd("read_job", url)

    def _fill_manual(self, job):
        for k in ("company", "title", "location", "salary"):
            if job.get(k):
                self.m[k].set(job[k])
        self.m_jd.delete("1.0", "end")
        self.m_jd.insert("1.0", job.get("jd", ""))

    def _save_manual(self):
        job = {k: self.m[k].get().strip() for k in ("url", "company", "title", "location", "salary")}
        job["jd"] = self.m_jd.get("1.0", "end").strip()
        if not job["company"] or not job["title"] or not job["url"]:
            return messagebox.showwarning(config.APP_NAME, "Job link, company and title are needed.")
        self._collect_settings(quiet=True)
        self._cmd("save_manual", job, {k: self.m[k].get().strip() for k in ("folder", "resume", "cover", "notes")})

    # ---------- history ----------
    def _history_tab(self):
        t = self.tab_his
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=self._history).pack(side="left")
        ttk.Button(bar, text="Open folder", command=lambda: self._open_selected(self.htree, self.h_folder, folder=True)).pack(side="left", padx=8)
        ttk.Label(bar, text="Double-click a row to open the job. Rows marked 'attention' still have their tab open in the bot's browser.", style="Muted.TLabel").pack(side="left", padx=8)
        cols = ("when", "status", "company", "title", "ats", "why")
        self.htree = ttk.Treeview(t, columns=cols, show="headings")
        for c, w in zip(cols, (130, 90, 200, 300, 50, 320)):
            self.htree.heading(c, text=c.upper() if c == "ats" else c.capitalize())
            self.htree.column(c, width=w, anchor="w")
        self.htree.pack(fill="both", expand=True, pady=(10, 0))
        self.htree.tag_configure("applied", foreground="#15803d")
        self.htree.tag_configure("attention", foreground="#b45309")
        self.htree.tag_configure("failed", foreground="#b91c1c")
        self.htree.tag_configure("skipped", foreground="#8a90a0")
        self.htree.bind("<Double-1>", lambda e: self._open_selected(self.htree, self.h_url))
        self.h_url: dict[str, str] = {}
        self.h_folder: dict[str, str] = {}

    def _history(self):
        self.htree.delete(*self.htree.get_children())
        for i, r in enumerate(reversed(ledger(400))):
            iid = str(i)
            self.htree.insert("", "end", iid=iid, tags=(r.get("status"),), values=(
                datetime.fromtimestamp(r["t"]).strftime("%m-%d %H:%M"), r.get("status"), r.get("company", ""), r.get("title", ""),
                r.get("ats") or "", r.get("why", "")))
            self.h_url[iid] = r.get("url", "")
            self.h_folder[iid] = r.get("folder", "")

    def _open_selected(self, tree, mapping, folder=False):
        sel = tree.selection()
        if not sel:
            return
        target = mapping.get(sel[0], "")
        if not target:
            return
        self._open_path(target) if folder else webbrowser.open(target)

    def _open_path(self, path):
        path = os.path.expanduser(path)
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def _quit(self):
        if self.worker.busy and not messagebox.askyesno(config.APP_NAME, "The bot is working. Stop and close?"):
            return
        self.worker.stop.set()
        self.worker.cmd.put(("quit",))
        self.after(600, self.destroy)


if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception:
        # pythonw has no console: write the error down and show it, instead of closing silently
        import traceback
        err = traceback.format_exc()
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        (config.LOG_DIR / "crash.log").write_text(err, encoding="utf-8")
        try:
            r = tk.Tk()
            r.withdraw()
            messagebox.showerror(config.APP_NAME, f"Dice Auto Bid could not start:\n\n{err[-1500:]}\n\nSaved in data\\logs\\crash.log")
        except Exception:
            pass
        raise
