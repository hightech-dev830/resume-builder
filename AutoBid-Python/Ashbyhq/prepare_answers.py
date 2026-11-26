"""Prepare answers for selected Ashby jobs / questions from your saved answers.

    python prepare_answers.py --jobs-file data/selection.json --out data/prepare_result.json
    python prepare_answers.py --keys-file data/prepare_keys.json --out data/prepare_result.json

See common/prepare_runner.py: shared answers come back as drafts for the app; per-job
answers (AI-writes questions) are saved for each job and used by Apply.
"""
from config import stop_requested  # first: puts AutoBid-Python/common on sys.path
import answers as bank_mod
import db
from browser import open_browser
from prepare_runner import run

if __name__ == "__main__":
    run(bank_mod, db, open_browser, stop_requested, "ashby")
