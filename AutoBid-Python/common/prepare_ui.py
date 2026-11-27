"""Tk app side of "Prepare answers" (mixed into each bot's App).

  Jobs tab      "Prepare answers"   -> every unanswered required question of the ticked jobs
  Answers tab   "Prepare selected"  -> the selected questions (Ctrl/Shift-click for several);
                                       with an "Answers for selected" job filter, for those jobs

When the background run finishes, shared answers are saved to Supabase at once (so those
questions show as answered) and per-job answers are already stored per job. The editor of an "AI writes" question shows each job's own answer to edit.

The App provides: conn, bank, pending_answers, qlist, tabs, answers_tab, e_perjob (a frame),
selected_ids(), run_script(), flush_pending_answers(), show_answers_for_ids(),
load_bank_list(), load_jobs(), _update_draft_ui(), log().
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

PREPARE_LABEL = "Preparing answers"
LIVE_TAG = "@@AB_PREPARED "          # same as prepare_runner.LIVE_TAG
TEXT_TYPES = ("String", "LongText")


def _paths():
    from config import DATA_DIR   # the bot's own config (cwd = bot folder)
    return DATA_DIR / "prepare_result.json", DATA_DIR / "prepare_keys.json", \
        DATA_DIR / "prepare_jobs.json"


def _show(v):
    if isinstance(v, list):
        return ", ".join(map(str, v))
    return "" if v is None else str(v)


class PrepareAnswersMixin:
    # ------------------------------------------------------------------ start

    def prepare_answers_for_jobs(self):
        ids = self.selected_ids()
        if not ids:
            messagebox.showinfo("Prepare answers", "Tick one or more jobs first.")
            return
        self._start_prepare(ids, None, f"{len(ids)} job(s)")

    def prepare_answers_for_questions(self):
        sel = self.qlist.selection()
        if not sel:
            messagebox.showinfo("Prepare answers",
                                "Select one or more questions in the list first "
                                "(Ctrl-click / Shift-click for several).")
            return
        keys = [self.bank["questions"][int(i)]["key"] for i in sel]
        ids = list(getattr(self, "answers_filter_ids", None) or [])
        what = f"{len(keys)} question(s)" + (f" for {len(ids)} job(s)" if ids else "")
        self._start_prepare(ids, keys, what)

    def _start_prepare(self, ids, keys, what):
        out, keys_file, jobs_file = _paths()
        # the AI must see your latest answers: save drafts first
        self.flush_pending_answers(reason="before Prepare answers")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.unlink(missing_ok=True)
        args = ["prepare_answers.py", "--out", str(out)]
        if ids:
            jobs_file.write_text(json.dumps(list(ids)), encoding="utf-8")
            args += ["--jobs-file", str(jobs_file)]
        if keys:
            keys_file.write_text(json.dumps(list(keys)), encoding="utf-8")
            args += ["--keys-file", str(keys_file)]
        self._prepare_ids = list(ids)
        if hasattr(self, "_flush_ai_settings"):
            self._flush_ai_settings()
        self.run_script(args, f"{PREPARE_LABEL}: {what}")

    # ------------------------------------------------------------------ finish

    def _prepare_finished(self):
        """Call after the run's output is drained (and the bank reloaded / board refreshed)."""
        out, _, _ = _paths()
        try:
            res = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            self.log("! Prepare answers: no result - see the log above.\n")
            return
        import answers as bank_mod
        ids = getattr(self, "_prepare_ids", [])
        # Open the Answers tab first (with the tab-change save off), then save everything once.
        self._suppress_tab_flush = True
        try:
            if ids:
                self.show_answers_for_ids(ids)
            else:
                self.tabs.select(self.answers_tab)
            self.update_idletasks()
        finally:
            self.after(400, lambda: setattr(self, "_suppress_tab_flush", False))
        # The run already saved each batch to Supabase; make sure the (reloaded) bank shows all
        # of them, and retry the ones Supabase refused.
        shared = res.get("shared") or {}
        unsaved = res.get("unsaved") or {}
        for key, ans in shared.items():
            try:
                self.bank = bank_mod.set_answer_local(key, ans, self.bank)
            except KeyError:
                continue
            if key in unsaved:
                self.pending_answers[key] = ans
        self._answers_dirty = True
        if unsaved:
            self.flush_pending_answers(reason="Prepare answers retry")
        if self._answers_dirty:
            self.resolved = bank_mod.resolve_all(self.conn, self.bank)
            self._answers_dirty = False
            self.load_jobs()
        self.load_bank_list()
        self._update_draft_ui()
        if self.selected_q and not self._editor_busy():
            self.edit_question()
        skipped = res.get("skipped") or []
        left = sum(1 for k in unsaved if k in self.pending_answers)
        shared_line = (f"✓ {len(shared)} shared answer(s) prepared and saved to Supabase — "
                       "edit them in this list as usual." if not left else
                       f"! {len(shared)} shared answer(s) prepared; {left} NOT saved (Supabase "
                       "error) — they are the blue drafts, press “Save answers” to retry.")
        lines = [shared_line,
                 f"✓ {res.get('per_job', 0)} per-job answer(s) saved for {res.get('jobs', 0)} job(s) — "
                 "select an “AI writes” question to see and edit each job's answer."]
        if skipped:
            lines.append(f"\n{len(skipped)} left for you:")
            for s in skipped[:10]:
                where = f"[{s['job'][:30]}] " if s.get("job") else ""
                lines.append(f"  • {where}{s['question'][:70]} — {s['why']}")
            if len(skipped) > 10:
                lines.append(f"  … and {len(skipped) - 10} more (see the log)")
        messagebox.showinfo("Prepare answers - done", "\n".join(lines))

    # ------------------------------------------------------------------ live updates

    def _editor_busy(self):
        """True while you are typing in the answer editor (do not redraw it under you)."""
        try:
            w = str(self.focus_get() or "")
        except Exception:
            return False
        holders = [getattr(self, "e_holder", None), getattr(self, "e_perjob", None)]
        return any(h is not None and w.startswith(str(h)) for h in holders)

    def prepare_live_line(self, line):
        """A "@@AB_PREPARED {...}" line from the running prepare: show what was just saved.
        Returns True when the line was one of those (do not print it)."""
        if not line.startswith(LIVE_TAG):
            return False
        try:
            msg = json.loads(line[len(LIVE_TAG):].strip())
        except Exception:
            return True
        import answers as bank_mod
        by_key = {q["key"]: q for q in self.bank.get("questions") or []}
        sel_key = (self.selected_q or {}).get("key") if getattr(self, "selected_q", None) else None
        redraw = False
        shared = {k: v for k, v in (msg.get("shared") or {}).items() if k in by_key}
        for key, ans in shared.items():
            self.bank = bank_mod.set_answer_local(key, ans, self.bank)
            if msg.get("saved"):
                self.pending_answers.pop(key, None)
            else:
                self.pending_answers[key] = ans          # Supabase refused: keep as a draft
            self._update_q_row(by_key[key])
            redraw = redraw or key == sel_key
        if shared:
            self._update_draft_ui()
            self.log(f"✓ {len(shared)} answer(s) {'saved' if msg.get('saved') else 'prepared'} "
                     f"— updated in the Answers list\n")
        if msg.get("job") and sel_key:
            qids = msg.get("qids") or []
            if qids and self.conn.execute(
                    f"SELECT 1 FROM questions WHERE bank_key=? AND id IN "
                    f"({','.join('?' * len(qids))}) LIMIT 1", [sel_key, *qids]).fetchone():
                redraw = True
        self._answers_dirty = True
        if redraw and not self._editor_busy():
            self.edit_question()
        return True

    def _prepared_counts(self):
        """{bank_key: number of active jobs with a per-job prepared answer} for the list."""
        try:
            return {r[0]: r[1] for r in self.conn.execute(
                "SELECT q.bank_key, COUNT(DISTINCT q.job_id) FROM questions q "
                "JOIN jobs j ON j.id = q.job_id WHERE q.prepared IS NOT NULL "
                "AND j.is_active = 1 GROUP BY q.bank_key")}
        except Exception:
            return {}

    # ------------------------------------------------------------------ per-job editor

    def _build_per_job_frame(self, parent):
        self.e_perjob = ttk.Frame(parent)
        self._pj_rows = []
        self._pj_after = None
        self._pj_ignore = False

    def _show_per_job_answers(self, q):
        """Below the shared answer: this question's answer per job (Prepare answers / your own)."""
        f = self.e_perjob
        for w in f.winfo_children():
            w.destroy()
        self._pj_rows = []
        filt = list(getattr(self, "answers_filter_ids", None) or [])
        sql = ("SELECT q.id qid, q.prepared, j.id jid, COALESCE(NULLIF(j.company,''), j.org) co, "
               "j.title FROM questions q JOIN jobs j ON j.id = q.job_id "
               "WHERE q.bank_key = ? AND j.is_active = 1")
        params = [q["key"]]
        if filt:
            sql += f" AND j.id IN ({','.join('?' * len(filt))})"
            params += filt
        elif q.get("type") in TEXT_TYPES:
            sql += " AND q.prepared IS NOT NULL"
        else:
            f.pack_forget()
            return
        rows = self.conn.execute(sql + " ORDER BY co, j.title", params).fetchall()
        if not rows or (q.get("type") not in TEXT_TYPES
                        and not any(r["prepared"] for r in rows)):
            f.pack_forget()
            return
        self._pj_rows = rows
        f.pack(fill="x", pady=(2, 4), after=self.e_holder)
        n_prep = sum(1 for r in rows if r["prepared"])
        ttk.Label(f, text=f"Per-job answer — used by Apply for that job only "
                          f"({n_prep}/{len(rows)} job(s) have one):",
                  style="Stat.TLabel").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.pack(fill="x")
        self._pj_pick = ttk.Combobox(bar, state="readonly", width=70, values=[
            f"{'✓' if r['prepared'] else '·'} {r['co']} — {r['title']}" for r in rows])
        self._pj_pick.pack(side="left")
        self._pj_pick.bind("<<ComboboxSelected>>", lambda e: self._pj_load())
        ttk.Button(bar, text="Clear for this job", command=self._pj_clear).pack(
            side="left", padx=6)
        self._pj_note = ttk.Label(bar, text="", foreground="#1a7f37")
        self._pj_note.pack(side="left", padx=6)
        self._pj_text = tk.Text(f, height=4, wrap="word", font=("Segoe UI", 10))
        self._pj_text.pack(fill="x", pady=(2, 0))
        self._pj_text.bind("<KeyRelease>", self._pj_schedule)
        self._pj_text.bind("<FocusOut>", lambda e: self._pj_save())
        first = next((i for i, r in enumerate(rows) if r["prepared"]), 0)
        self._pj_pick.current(first)
        self._pj_load()

    def _pj_current(self):
        i = self._pj_pick.current() if getattr(self, "_pj_pick", None) else -1
        return self._pj_rows[i] if 0 <= i < len(self._pj_rows) else None

    def _pj_load(self):
        r = self._pj_current()
        self._pj_ignore = True
        try:
            self._pj_text.delete("1.0", "end")
            if r and r["prepared"]:
                try:
                    v = json.loads(r["prepared"])
                except Exception:
                    v = r["prepared"]
                self._pj_text.insert("1.0", _show(v))
            self._pj_note.config(text="" if r and r["prepared"] else
                                 "(empty: the AI writes it at Apply)")
        finally:
            self._pj_ignore = False

    def _pj_schedule(self, _e=None):
        if self._pj_ignore:
            return
        if self._pj_after:
            self.after_cancel(self._pj_after)
        self._pj_after = self.after(600, self._pj_save)

    def _pj_write(self, value):
        r = self._pj_current()
        if not r:
            return
        self.conn.execute("UPDATE questions SET prepared=? WHERE id=?",
                          (None if value is None else json.dumps(value, ensure_ascii=False),
                           r["qid"]))
        self.conn.commit()
        self._answers_dirty = True       # the Jobs board refreshes on the next save / tab change
        i = self._pj_pick.current()
        rows = list(self._pj_rows)
        rows[i] = dict(r, prepared=None if value is None else json.dumps(value))
        self._pj_rows = rows
        vals = list(self._pj_pick["values"])
        vals[i] = f"{'✓' if value is not None else '·'} {r['co']} — {r['title']}"
        self._pj_pick["values"] = vals
        self._pj_pick.current(i)

    def _pj_save(self):
        self._pj_after = None
        if self._pj_ignore or not getattr(self, "_pj_text", None):
            return
        r = self._pj_current()
        if not r:
            return
        text = self._pj_text.get("1.0", "end").strip()
        old = r["prepared"]
        try:
            old = json.loads(old) if old else None
        except Exception:
            pass
        if (text or None) == (_show(old) or None):
            return
        self._pj_write(text or None)
        self._pj_note.config(text="✓ saved for this job" if text else
                             "(empty: the AI writes it at Apply)")

    def _pj_clear(self):
        if not self._pj_current():
            return
        self._pj_write(None)
        self._pj_load()
