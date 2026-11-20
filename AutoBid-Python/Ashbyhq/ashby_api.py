"""Thin client for Ashby's public (no-login) job APIs. Standard library only.

Two-stage scraping:
  stage 1  fetch_job_list(slug)        - every job on a company board: title, department
                                         path, locations, workplace, pay summary (small)
  stage 2  fetch_job_details(slug, id) - one job: description, pay per region, publish
                                         date, application + survey questions
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from config import TIMEOUT
from html_text import html_to_text

GRAPHQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql?op={op}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}

_LIST_QUERY = """
query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
  jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
    teams { id name parentTeamId }
    jobPostings {
      id title teamId locationName workplaceType employmentType compensationTierSummary
      secondaryLocations { locationName }
    }
  }
}
"""

_FORM_FIELDS = """
      id
      sections {
        title
        fieldEntries { ... on FormFieldEntry { id isRequired descriptionHtml field } }
      }
"""

_DETAILS_QUERY = """
query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName,
             jobPostingId: $jobPostingId) {
    id title departmentName teamNames locationName secondaryLocationNames
    workplaceType employmentType publishedDate descriptionHtml
    compensationTierSummary scrapeableCompensationSalarySummary
    compensationTiers { title tierSummary }
    applicationForm {""" + _FORM_FIELDS + """}
    surveyForms {""" + _FORM_FIELDS + """}
  }
}
"""


RETRIES = 5


def _request(url, payload=None):
    """HTTP request with backoff: on 429 (too many requests) or a 5xx error it waits
    (Retry-After if Ashby sends one, else 5, 10, 20, 40 s) and tries again."""
    data = None
    headers = dict(HEADERS)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if (e.code != 429 and e.code < 500) or attempt == RETRIES - 1:
                raise
            retry_after = e.headers.get("Retry-After") if e.headers else None
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 5 * 2 ** attempt
        except (urllib.error.URLError, TimeoutError):
            if attempt == RETRIES - 1:
                raise
            wait = 3 * (attempt + 1)
        time.sleep(min(wait, 120) + random.random())


def _graphql(op, query, variables):
    body = _request(GRAPHQL_URL.format(op=op), {"operationName": op, "variables": variables, "query": query})
    if body.get("errors"):
        raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
    return body.get("data") or {}


def fetch_company_name(org):
    """Display name of the company behind a job board slug (e.g. "Ashby")."""
    data = _graphql(
        "ApiOrganizationFromHostedJobsPageName",
        "query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!, "
        "$searchContext: OrganizationSearchContext) { organization: "
        "organizationFromHostedJobsPageName(organizationHostedJobsPageName: "
        "$organizationHostedJobsPageName, searchContext: $searchContext) { name } }",
        {"organizationHostedJobsPageName": org, "searchContext": "JobBoard"})
    return (data.get("organization") or {}).get("name") or org


# ---------------------------------------------------------------- stage 1

def fetch_job_list(org):
    """Stage 1: all listed jobs of a board, without descriptions (fast, small)."""
    board = _graphql("ApiJobBoardWithTeams", _LIST_QUERY,
                     {"organizationHostedJobsPageName": org}).get("jobBoard")
    if not board:
        raise RuntimeError("no job board")
    teams = {t["id"]: t for t in board.get("teams") or []}

    def team_path(tid):
        names = []
        while tid and tid in teams and len(names) < 6:
            names.insert(0, teams[tid]["name"])
            tid = teams[tid].get("parentTeamId")
        return names

    jobs = []
    for p in board.get("jobPostings") or []:
        path = team_path(p.get("teamId"))
        base = f"https://jobs.ashbyhq.com/{urllib.parse.quote(org)}/{p['id']}"
        jobs.append({
            "id": p["id"],
            "title": (p.get("title") or "").strip(),
            "department": path[0] if path else "",
            "team": path[-1] if path else "",
            "dept_path": " > ".join(path),
            "location": p.get("locationName") or "",
            "locations": [p.get("locationName") or ""] +
                         [s.get("locationName") or "" for s in p.get("secondaryLocations") or []],
            "workplace_type": p.get("workplaceType"),
            "employment_type": p.get("employmentType"),
            "compensation": p.get("compensationTierSummary") or "",
            "job_url": base,
            "apply_url": base + "/application",
        })
    return jobs


# ---------------------------------------------------------------- stage 2

def _flatten_form(form, form_kind):
    questions = []
    for section in (form or {}).get("sections") or []:
        for entry in section.get("fieldEntries") or []:
            field = entry.get("field") or {}
            if not field or field.get("isDeactivated"):
                continue
            options = [
                {"label": o.get("label"), "value": o.get("value")}
                for o in (field.get("selectableValues") or [])
            ]
            questions.append({
                "id": entry["id"],
                "form": form_kind,              # "application" or "survey"
                "form_id": form.get("id"),
                "section": section.get("title") or "",
                "path": field.get("path"),
                "title": field.get("title") or field.get("humanReadablePath") or "",
                "type": field.get("type"),
                "required": bool(entry.get("isRequired")),
                "description": html_to_text(entry.get("descriptionHtml")),
                "options": options,
                "raw": field,
            })
    return questions


def fetch_job_details(org, job_id):
    """Stage 2: (details, questions) for one job. details is None if the job is gone."""
    posting = _graphql("ApiJobPosting", _DETAILS_QUERY,
                       {"organizationHostedJobsPageName": org, "jobPostingId": job_id}).get("jobPosting")
    if not posting:
        return None, []
    tiers = "\n".join(
        f"{t.get('title')}: {t.get('tierSummary')}" if t.get("title") else (t.get("tierSummary") or "")
        for t in posting.get("compensationTiers") or [])
    details = {
        "description": html_to_text(posting.get("descriptionHtml")),
        "description_html": posting.get("descriptionHtml") or "",
        "compensation": posting.get("compensationTierSummary")
                        or posting.get("scrapeableCompensationSalarySummary") or "",
        "compensation_tiers": tiers,
        "published_at": posting.get("publishedDate"),
        "locations": [posting.get("locationName") or ""] + list(posting.get("secondaryLocationNames") or []),
    }
    questions = _flatten_form(posting.get("applicationForm"), "application")
    for survey in posting.get("surveyForms") or []:
        questions += _flatten_form(survey, "survey")
    return details, questions


def fetch_questions(org, job_id):
    """Only the questions of one job (kept for older callers)."""
    return fetch_job_details(org, job_id)[1]
