"""AutoBid - Wellfound desktop app.

    python app.py   (or AutoBid-Wellfound.bat)

Flow: Collect company jobs → fill Answers/My profile → Apply
  (apply opens ?autoOpenApplication=true, submits, then pauses on email verify).
"""
import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from config import BASE_DIR, BATCH_SIZE, PAUSE_FLAG, STOP_FLAG, VERIFY_FLAG
import answers as bank_mod
import db

PY = sys.executable.replace("pythonw.exe", "python.exe")
SETTINGS_PATH = BASE_DIR / "data" / "app_settings.json"


def load_settings():
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(s):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoBid · Wellfound")
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
        self._checked = set()
        self.current_id = None

        self._style()
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="AutoBid · Wellfound", font=("Segoe UI Semibold", 16)).pack(side="left")
        ttk.Button(top, text="Open bot browser", command=self.open_bot_browser).pack(side="left", padx=12)
        self.stats = ttk.Label(top, text="", foreground="#444")
        self.stats.pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._jobs_tab()
        self._answers_tab()
        self._run_tab()

        logf = ttk.LabelFrame(self, text="Log", padding=6)
        logf.pack(fill="both", expand=False, padx=10, pady=(0, 10))
        self.logbox = ScrolledText(logf, height=12, font=("Consolas", 9), wrap="word")
        self.logbox.pack(fill="both", expand=True)

        self.refresh()
        self.after(150, self._drain)

    def _style(self):
        s = ttk.Style(self)
        if "vista" in s.theme_names():
            s.theme_use("vista")

    # ---- tabs
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
        ttk.Button(bar, text="Collect jobs", command=self.collect).pack(side="right")
        ttk.Button(bar, text="Collect (limit 30)",
                   command=lambda: self.run_script(
                       ["collect.py", "--limit", "30"], "Collect (30 companies)")).pack(
            side="right", padx=4)

        cols = ("chk", "status", "company", "title", "posted", "pay", "remote")
        self.jobs = ttk.Treeview(tab, columns=cols, show="headings", selectmode="extended")
        for c, t, w in [("chk", "☐", 34), ("status", "Status", 80), ("company", "Company", 120),
                        ("title", "Title", 280), ("posted", "Posted", 120),
                        ("pay", "Pay", 120), ("remote", "Remote", 60)]:
            self.jobs.heading(c, text=t)
            self.jobs.column(c, width=w, stretch=c == "title")
        self.jobs.pack(fill="both", expand=True)
        self.jobs.bind("<Button-1>", self._on_click)
        btns = ttk.Frame(tab)
        btns.pack(fill="x", pady=6)
        ttk.Button(btns, text="Select all shown", command=self.select_all).pack(side="left")
        ttk.Button(btns, text="Clear", command=self.clear_sel).pack(side="left", padx=4)
        ttk.Button(btns, text="Open job", command=self.open_job).pack(side="left", padx=4)
        ttk.Button(btns, text="Dry run", command=lambda: self.apply(True)).pack(side="right", padx=4)
        ttk.Button(btns, text="Apply", command=lambda: self.apply(False)).pack(side="right")

    def _answers_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="  Answers  ")
        box = ttk.LabelFrame(tab, text="  My profile (Wellfound apply form — Picture 5)  ", padding=10)
        box.pack(fill="x")
        self.p_entries = {}
        fields = [
            ("name", "Full Name"), ("email", "Email"), ("password", "Password (Wellfound account)"),
            ("phone", "Phone"), ("location", "Location"),
            ("years_experience", "Years of experience"), ("desired_salary", "Desired salary (USD)"),
            ("linkedin", "LinkedIn URL"), ("github", "GitHub URL"),
            ("authorized_us", "Authorized to work in US (true/false)"),
            ("sponsorship", "Need visa sponsorship (true/false)"),
            ("open_remote", "Open to remote (true/false)"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", pady=2)
            show = "•" if key == "password" else ""
            e = ttk.Entry(box, width=56, show=show)
            e.grid(row=i, column=1, sticky="w", padx=8, pady=2)
            e.insert(0, self.bank["profile"].get(key) or "")
            self.p_entries[key] = e
        ttk.Button(box, text="Save profile", command=self.save_profile).grid(
            row=len(fields), column=1, sticky="e", pady=8)
        ttk.Label(tab, foreground="#444", wraplength=900, text=(
            "These map to Wellfound's default application fields. Extra per-job questions "
            "appear in the bank after Collect (stage 2). Email verification after Submit "
            "is done by you in the bot Chrome window.")).pack(anchor="w", pady=10)

    def _run_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text="  Run  ")
        ttk.Label(tab, text=(
            "1) Open bot browser → sign in to Wellfound\n"
            "2) Collect jobs (companies.csv → company /jobs pages → job details + questions)\n"
            "3) Fill Answers / My profile\n"
            "4) Select jobs → Apply  (uses ?autoOpenApplication=true)\n"
            "5) When email verify appears, enter the 6-digit code in Chrome, then Continue"
        ), justify="left").pack(anchor="w")
        row = ttk.Frame(tab, padding=(0, 12, 0, 0))
        row.pack(fill="x")
        ttk.Button(row, text="Collect all", command=self.collect).pack(side="left")
        ttk.Button(row, text="■ Stop", command=self.stop).pack(side="left", padx=6)
        self.pause_btn = ttk.Button(row, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side="left")
        self.busy = ttk.Label(tab, text="", foreground="#b35900")
        self.busy.pack(anchor="w", pady=8)

    # ---- jobs helpers
    def load_jobs(self):
        sql = "SELECT * FROM jobs WHERE is_active=1"
        if self.f_rel.get() == "relevant":
            sql += " AND relevant=1"
        elif self.f_rel.get() == "not relevant":
            sql += " AND relevant=0"
        sql += " ORDER BY COALESCE(published_at, first_seen_at) DESC"
        rows = self.conn.execute(sql).fetchall()
        keep = set(self.jobs.selection())
        self.jobs.delete(*self.jobs.get_children())
        for r in rows:
            self.jobs.insert("", "end", iid=r["id"], values=(
                "☑" if r["id"] in self._checked else "☐",
                r["status"], r["company"] or r["org"], r["title"],
                (r["published_at"] or r["first_seen_at"] or "")[:16],
                r["compensation"] or "", "yes" if r["is_remote"] else "no"))
        for i in keep:
            if self.jobs.exists(i):
                self.jobs.selection_add(i)
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1").fetchone()["c"]
        ready = self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE is_active=1 AND relevant=1 AND status='ready'"
        ).fetchone()["c"]
        self.stats.config(text=f"{n} relevant · {ready} ready · {len(self._checked)} selected")

    def refresh(self):
        try:
            bank_mod.sync_bank(self.conn, self.bank)
        except Exception:
            pass
        self.load_jobs()

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
            self.stats.config(text=self.stats.cget("text").split("·")[0] +
                              f"· {self.stats.cget('text').split('·')[1] if '·' in self.stats.cget('text') else ''} · {len(self._checked)} selected")

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

    def save_profile(self):
        vals = {k: e.get().strip() for k, e in self.p_entries.items()}
        self.bank = bank_mod.set_profile(vals, self.bank)
        messagebox.showinfo("Saved", "Profile saved to Supabase (shared Answers / My profile).")

    def open_bot_browser(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("Busy", "Stop the current task first.")
            return
        try:
            from browser import get_profile_dir
            import open_browser_login
            open_browser_login.main()
            self.log(f"Opened Wellfound Chrome ({get_profile_dir()}) — "
                     f"sign in, then close it before Apply "
                     f"(Ashby/Greenhouse use separate profiles).\n")
        except Exception as e:
            messagebox.showerror("Open bot browser", str(e))

    def collect(self):
        self.run_script(["collect.py"], "Collecting Wellfound companies")

    def apply(self, dry):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Apply", "Select one or more jobs first.")
            return
        if not dry and not messagebox.askyesno(
                "Apply", f"Submit applications to {len(ids)} Wellfound job(s)?"):
            return
        path = BASE_DIR / "data" / "selection.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ids), encoding="utf-8")
        args = ["bid.py", "--jobs-file", str(path), "--batch", str(BATCH_SIZE)]
        if dry:
            args.append("--dry-run")
        self.run_script(args, f"{'Dry run' if dry else 'Applying'}: {len(ids)} jobs")

    def toggle_pause(self):
        if PAUSE_FLAG.exists():
            PAUSE_FLAG.unlink(missing_ok=True)
            self.pause_btn.config(text="Pause")
            self.log("▶ Continued\n")
        else:
            PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
            PAUSE_FLAG.write_text("pause", encoding="utf-8")
            self.pause_btn.config(text="Continue")
            self.log("⏸ Paused\n")

    def stop(self):
        PAUSE_FLAG.unlink(missing_ok=True)
        STOP_FLAG.parent.mkdir(parents=True, exist_ok=True)
        STOP_FLAG.write_text("stop", encoding="utf-8")
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.log("■ Stop requested\n")

    def run_script(self, args, label):
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("Busy", "Something is already running.")
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

    def _drain(self):
        try:
            while True:
                line = self.out_q.get_nowait()
                if line is None:
                    self.busy.config(text="")
                    self.log("━━ finished ━━\n")
                    self.refresh()
                    if VERIFY_FLAG.exists():
                        self.pause_btn.config(text="Continue")
                        messagebox.showinfo(
                            "Email verification",
                            "Wellfound asked for a 6-digit email code.\n"
                            "Enter it in the bot Chrome window, then click Continue.")
                else:
                    self.log(line)
        except queue.Empty:
            pass
        self.after(150, self._drain)


if __name__ == "__main__":
    App().mainloop()
