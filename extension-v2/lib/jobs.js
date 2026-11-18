// Job feed. Pulls postings from public job APIs and company job boards, normalizes them to one shape,
// tags each with category / seniority / work mode / ATS platform, de-duplicates and caches them locally.
//
// Sources (keys go in Settings → Jobs; the ones without a key work out of the box):
//   remotive   remotive.com/api           remote tech jobs, no key (they ask for at most a few calls a day: cached 6 h)
//   himalayas  himalayas.app/jobs/api     remote jobs, no key
//   jobicy     jobicy.com/api/v2          remote jobs, no key
//   remoteok   remoteok.com/api           remote jobs, no key
//   arbeitnow  arbeitnow.com/api          EU-leaning jobs, no key
//   adzuna     api.adzuna.com             large US/UK index, free app id + key from developer.adzuna.com
//   jsearch    jsearch (RapidAPI)         Google for Jobs (LinkedIn, Indeed, Glassdoor, company sites); RapidAPI key
//   boards     greenhouse / lever / ashby public job boards of the companies you list: direct ATS links, no key
import { htmlToText, hostOf, sleep } from './util.js';
import { findTech } from './ats.js';

export const SOURCES = { manual: 'Manual', remotive: 'Remotive', himalayas: 'Himalayas', jobicy: 'Jobicy', remoteok: 'Remote OK', arbeitnow: 'Arbeitnow', adzuna: 'Adzuna', jsearch: 'JSearch', greenhouse: 'Greenhouse board', lever: 'Lever board', ashby: 'Ashby board' };

export const CATEGORIES = [
  ['AI / ML', /\b(machine learning|ml|ai|llm|genai|deep learning|nlp|computer vision|data scien|applied scien|research (engineer|scientist)|mlops)\b/i],
  ['Data', /\b(data (engineer|platform|infrastructure|warehouse)|analytics engineer|etl|big data|bi engineer|database)\b/i],
  ['DevOps / SRE / Cloud', /\b(devops|sre|site reliability|platform engineer|infrastructure|cloud (engineer|architect)|kubernetes|devsecops|release engineer|build engineer)\b/i],
  ['Security', /\b(security|appsec|infosec|penetration|cyber)\b/i],
  ['Mobile', /\b(ios|android|mobile|react native|flutter)\b/i],
  ['QA / Test', /\b(qa|quality|test|sdet|automation engineer)\b/i],
  ['Frontend', /\b(front[- ]?end|frontend|ui engineer|web developer|react developer|angular|vue)\b/i],
  ['Full-stack', /\b(full[- ]?stack|fullstack)\b/i],
  ['Backend', /\b(back[- ]?end|backend|api|server|distributed|java|golang|go engineer|python|node|ruby|\.net|c#|php|scala|rust)\b/i],
  ['Embedded / Systems', /\b(embedded|firmware|systems engineer|kernel|fpga|hardware)\b/i],
  ['Engineering management', /\b(engineering manager|head of engineering|director of engineering|vp engineering|cto)\b/i],
  ['Software engineer', /\b(software|developer|engineer|programmer|swe)\b/i]
];
const TECH_TITLE = /\b(engineer|developer|programmer|architect|sre|devops|scientist|swe|qa|sdet|data|ml|ai|software|tech lead|cto)\b/i;

export const PLATFORMS = [
  ['Greenhouse', /greenhouse\.io/], ['Lever', /lever\.co/], ['Ashby', /ashbyhq\.com/], ['Workday', /myworkdayjobs\.com|workday/],
  ['iCIMS', /icims\.com/], ['SmartRecruiters', /smartrecruiters\.com/], ['Taleo', /taleo\.net/], ['Rippling', /rippling\.com|rippling-ats/],
  ['Workable', /workable\.com/], ['BambooHR', /bamboohr\.com/], ['Jobvite', /jobvite\.com/], ['Breezy', /breezy\.hr/], ['Recruitee', /recruitee\.com/],
  ['JazzHR', /applytojob\.com/], ['Teamtailor', /teamtailor\.com/], ['Personio', /personio\./], ['SuccessFactors', /successfactors|sapsf/],
  ['Oracle Cloud', /oraclecloud\.com/], ['Dover', /dover\.(io|com)/], ['Wellfound', /wellfound\.com|angel\.co/], ['LinkedIn', /linkedin\.com/], ['Indeed', /indeed\.com/]
];

const SENIORITY = [['Intern', /\bintern(ship)?\b/i], ['Junior', /\b(junior|jr\.?|entry|graduate|new grad|associate)\b/i], ['Principal', /\b(principal|distinguished|fellow)\b/i],
  ['Staff', /\bstaff\b/i], ['Lead', /\b(lead|head|manager|director)\b/i], ['Senior', /\b(senior|sr\.?|iii|iv)\b/i], ['Mid', /\b(mid|ii|intermediate)\b/i]];

export const categoryOf = (title) => (CATEGORIES.find(([, re]) => re.test(title)) || ['Other'])[0];
export const seniorityOf = (title, hint = '') => (SENIORITY.find(([, re]) => re.test(`${title} ${hint}`)) || ['Mid'])[0];
export const platformOf = (url) => (PLATFORMS.find(([, re]) => re.test(String(url || ''))) || [''])[0];

function workMode(text, remoteFlag) {
  if (remoteFlag === true) return 'Remote';
  const t = String(text || '');
  if (/\bhybrid\b/i.test(t)) return 'Hybrid';
  if (/\b(remote|anywhere|work from home|wfh|distributed)\b/i.test(t)) return 'Remote';
  return t ? 'Onsite' : 'Remote';
}

function jobType(s) {
  const t = String(s || '').toLowerCase().replace(/[_-]/g, ' ');
  if (/intern/.test(t)) return 'Internship';
  if (/part/.test(t)) return 'Part-time';
  if (/contract|temporary|temp|freelance|consult/.test(t)) return /freelance/.test(t) ? 'Freelance' : 'Contract';
  if (/full|permanent|regular/.test(t)) return 'Full-time';
  return '';
}

// "$120k - $150k", "120000-150000 USD" -> { min, max, currency }
function parseSalary(s) {
  const t = String(s || '');
  const cur = /€|eur/i.test(t) ? 'EUR' : /£|gbp/i.test(t) ? 'GBP' : /cad|c\$/i.test(t) ? 'CAD' : /\$|usd/i.test(t) ? 'USD' : '';
  const nums = [...t.matchAll(/(\d{1,3}(?:[,.]\d{3})+|\d+(?:\.\d+)?)\s*(k)?/gi)].map((m) => parseFloat(m[1].replace(/[,.](?=\d{3}\b)/g, '')) * (m[2] ? 1000 : 1)).filter((n) => n >= 10);
  if (!nums.length) return {};
  return { salaryMin: Math.min(...nums), salaryMax: Math.max(...nums), currency: cur };
}

function make(source, o) {
  const title = String(o.title || '').trim();
  const description = htmlToText(o.description || '').slice(0, 12000);
  const applyUrl = o.applyUrl || o.url || '';
  const loc = String(o.location || '').trim();
  return {
    id: `${source}:${o.id || o.url}`,
    source,
    title,
    company: String(o.company || '').trim(),
    location: loc || (o.remote ? 'Remote' : ''),
    workMode: workMode(`${loc} ${o.remoteText || ''} ${title}`, o.remote),
    type: jobType(o.type) || 'Full-time',
    url: o.url || applyUrl,
    applyUrl,
    description,
    tags: (o.tags || []).map(String).slice(0, 12),
    salaryMin: o.salaryMin || null,
    salaryMax: o.salaryMax || null,
    currency: o.currency || (o.salaryMin ? 'USD' : ''),
    postedAt: o.postedAt ? new Date(o.postedAt).getTime() || Date.now() : Date.now(),
    platform: platformOf(applyUrl) || platformOf(o.url),
    seniority: seniorityOf(title, o.level || ''),
    category: categoryOf(title),
    // technologies named in the posting (for Match % and the skills filter), computed once here
    tech: findTech(`${title}
${description}`).filter((t) => !t.generic).map((t) => t.name)
  };
}

async function getJson(url, init = {}) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${hostOf(url)} answered ${res.status}`);
  return res.json();
}

// ---------- sources ----------

const fetchers = {
  async remotive() {
    const d = await getJson('https://remotive.com/api/remote-jobs?category=software-dev&limit=300');
    return (d.jobs || []).map((j) => make('remotive', {
      id: j.id, title: j.title, company: j.company_name, url: j.url, description: j.description, tags: j.tags, type: j.job_type,
      location: j.candidate_required_location, remote: true, postedAt: j.publication_date, ...parseSalary(j.salary)
    }));
  },

  async himalayas() {
    const out = [];
    for (let offset = 0; offset < 200; offset += 20) {
      const d = await getJson(`https://himalayas.app/jobs/api?limit=20&offset=${offset}`);
      const jobs = d.jobs || [];
      out.push(...jobs.map((j) => make('himalayas', {
        id: j.guid || j.applicationLink, title: j.title, company: j.companyName, url: j.applicationLink || j.guid, description: j.description || j.excerpt,
        tags: [...(j.categories || []), ...(j.parentCategories || [])], type: j.employmentType, location: (j.locationRestrictions || []).join(', ') || 'Remote',
        remote: true, postedAt: j.pubDate ? j.pubDate * 1000 : null, level: (j.seniority || []).join(' '),
        salaryMin: j.minSalary || null, salaryMax: j.maxSalary || null, currency: j.currency || j.salaryCurrency || ''
      })));
      if (jobs.length < 20) break;
      await sleep(300);
    }
    return out;
  },

  async jobicy() {
    const d = await getJson('https://jobicy.com/api/v2/remote-jobs?count=100&industry=engineering');
    const d2 = await getJson('https://jobicy.com/api/v2/remote-jobs?count=100&industry=dev').catch(() => ({ jobs: [] }));
    return [...(d.jobs || []), ...(d2.jobs || [])].map((j) => make('jobicy', {
      id: j.id, title: j.jobTitle, company: j.companyName, url: j.url, description: j.jobDescription || j.jobExcerpt, tags: [].concat(j.jobIndustry || []),
      type: [].concat(j.jobType || []).join(' '), location: j.jobGeo, remote: true, postedAt: j.pubDate, level: j.jobLevel,
      salaryMin: j.annualSalaryMin || null, salaryMax: j.annualSalaryMax || null, currency: j.salaryCurrency || ''
    }));
  },

  async remoteok() {
    const d = await getJson('https://remoteok.com/api');
    return (Array.isArray(d) ? d : []).filter((j) => j && j.id && j.position).map((j) => make('remoteok', {
      id: j.id, title: j.position, company: j.company, url: j.url, applyUrl: j.apply_url || j.url, description: j.description, tags: j.tags,
      location: j.location || 'Remote', remote: true, postedAt: j.date, salaryMin: j.salary_min || null, salaryMax: j.salary_max || null, currency: j.salary_min ? 'USD' : ''
    }));
  },

  async arbeitnow() {
    const out = [];
    for (let page = 1; page <= 3; page++) {
      const d = await getJson(`https://www.arbeitnow.com/api/job-board-api?page=${page}`);
      out.push(...(d.data || []).map((j) => make('arbeitnow', {
        id: j.slug, title: j.title, company: j.company_name, url: j.url, description: j.description, tags: j.tags, type: (j.job_types || []).join(' '),
        location: j.location, remote: !!j.remote, postedAt: j.created_at ? j.created_at * 1000 : null
      })));
    }
    return out;
  },

  async adzuna(s) {
    if (!s.adzunaId || !s.adzunaKey) return [];
    const out = [];
    for (let page = 1; page <= 4; page++) {
      const q = new URLSearchParams({ app_id: s.adzunaId, app_key: s.adzunaKey, results_per_page: '50', what: s.query || 'software engineer', max_days_old: String(s.maxAgeDays || 14), sort_by: 'date' });
      const d = await getJson(`https://api.adzuna.com/v1/api/jobs/${s.adzunaCountry || 'us'}/search/${page}?${q}`);
      out.push(...(d.results || []).map((j) => make('adzuna', {
        id: j.id, title: j.title, company: j.company?.display_name, url: j.redirect_url, description: j.description, type: `${j.contract_time || ''} ${j.contract_type || ''}`,
        location: j.location?.display_name, remoteText: j.title + ' ' + j.description, postedAt: j.created,
        salaryMin: j.salary_min || null, salaryMax: j.salary_max || null, currency: j.salary_min ? (s.adzunaCountry === 'gb' ? 'GBP' : 'USD') : ''
      })));
      if ((d.results || []).length < 50) break;
    }
    return out;
  },

  async jsearch(s) {
    if (!s.jsearchKey) return [];
    const q = new URLSearchParams({ query: `${s.query || 'software engineer'} remote in USA`, page: '1', num_pages: '5', date_posted: s.maxAgeDays <= 3 ? '3days' : 'week' });
    const d = await getJson(`https://jsearch.p.rapidapi.com/search?${q}`, { headers: { 'X-RapidAPI-Key': s.jsearchKey, 'X-RapidAPI-Host': 'jsearch.p.rapidapi.com' } });
    return (d.data || []).map((j) => make('jsearch', {
      id: j.job_id, title: j.job_title, company: j.employer_name, url: j.job_apply_link, description: j.job_description, type: j.job_employment_type,
      location: [j.job_city, j.job_state, j.job_country].filter(Boolean).join(', '), remote: !!j.job_is_remote,
      postedAt: j.job_posted_at_timestamp ? j.job_posted_at_timestamp * 1000 : j.job_posted_at_datetime_utc,
      tags: [j.job_publisher].filter(Boolean), salaryMin: j.job_min_salary || null, salaryMax: j.job_max_salary || null, currency: j.job_salary_currency || ''
    }));
  },

  async boards(s) {
    const list = String(s.boards || '').split(/[\n,]+/).map((x) => x.trim()).filter(Boolean);
    const out = [];
    const errors = [];
    for (const entry of list) {
      const [kind, slugRaw] = entry.includes(':') ? entry.split(':') : ['greenhouse', entry];
      const slug = slugRaw.trim();
      try {
        if (kind === 'greenhouse') {
          const d = await getJson(`https://boards-api.greenhouse.io/v1/boards/${slug}/jobs?content=true`);
          out.push(...(d.jobs || []).map((j) => make('greenhouse', {
            id: j.id, title: j.title, company: j.company_name || titleCase(slug), url: j.absolute_url, description: j.content,
            location: j.location?.name, postedAt: j.first_published || j.updated_at, tags: (j.departments || []).map((x) => x.name)
          })));
        } else if (kind === 'lever') {
          const d = await getJson(`https://api.lever.co/v0/postings/${slug}?mode=json`);
          out.push(...(Array.isArray(d) ? d : []).map((j) => make('lever', {
            id: j.id, title: j.text, company: titleCase(slug), url: j.hostedUrl, applyUrl: j.applyUrl,
            description: [j.descriptionPlain || j.description, ...(j.lists || []).map((l) => `${l.text}\n${l.content}`), j.additionalPlain || ''].join('\n'),
            location: j.categories?.location, type: j.categories?.commitment, remote: j.workplaceType === 'remote' || undefined,
            remoteText: j.workplaceType, postedAt: j.createdAt, tags: [j.categories?.team].filter(Boolean),
            salaryMin: j.salaryRange?.min || null, salaryMax: j.salaryRange?.max || null, currency: j.salaryRange?.currency || ''
          })));
        } else if (kind === 'ashby') {
          const d = await getJson(`https://api.ashbyhq.com/posting-api/job-board/${slug}?includeCompensation=true`);
          out.push(...(d.jobs || []).map((j) => make('ashby', {
            id: j.id, title: j.title, company: titleCase(slug), url: j.jobUrl, applyUrl: j.applyUrl, description: j.descriptionHtml || j.descriptionPlain,
            location: j.location, type: j.employmentType, remote: j.isRemote || undefined, remoteText: j.workplaceType, postedAt: j.publishedAt,
            tags: [j.department, j.team].filter(Boolean), ...ashbyPay(j.compensation)
          })));
        }
      } catch (err) {
        errors.push(`${entry}: ${err.message}`);
      }
      await sleep(150);
    }
    out.errors = errors;
    return out;
  }
};

const titleCase = (s) => String(s).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
function ashbyPay(c) {
  const comp = c?.summaryComponents?.find((x) => x.compensationType === 'Salary') || c?.compensationTiers?.[0]?.components?.find((x) => x.compensationType === 'Salary');
  return comp ? { salaryMin: comp.minValue || null, salaryMax: comp.maxValue || null, currency: comp.currencyCode || '' } : {};
}

const dedupeKey = (j) => `${j.company} ${j.title}`.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

// Fetches every enabled source; returns { jobs, report } where report lists counts and errors per source.
export async function fetchAll(settings, onProgress = () => {}) {
  const s = settings.jobs;
  const report = [];
  const all = [];
  for (const [name, on] of Object.entries(s.sources)) {
    if (!on || !fetchers[name]) continue;
    onProgress(`Fetching ${SOURCES[name] || name}…`);
    try {
      const jobs = await fetchers[name](s);
      all.push(...jobs);
      report.push({ source: name, count: jobs.length, errors: jobs.errors || [] });
    } catch (err) {
      report.push({ source: name, count: 0, errors: [err.message] });
    }
  }
  const cutoff = Date.now() - (s.maxAgeDays || 14) * 86400000;
  const seen = new Map();
  for (const j of all) {
    if (!j.title || !j.url) continue;
    if (s.techOnly && !TECH_TITLE.test(j.title)) continue;
    if (j.postedAt < cutoff) continue;
    const k = dedupeKey(j);
    const prev = seen.get(k);
    // keep the copy with a direct ATS link, else the newest
    if (!prev || (!prev.platform && j.platform) || (j.postedAt > prev.postedAt && !!j.platform === !!prev.platform)) seen.set(k, j);
  }
  return { jobs: [...seen.values()].sort((a, b) => b.postedAt - a.postedAt).slice(0, 6000), report };
}

// ---------- cache ----------

export async function getCache() {
  const { jobs } = await chrome.storage.local.get('jobs');
  return jobs || { fetchedAt: 0, items: [], report: [] };
}

export async function refresh(settings, onProgress) {
  const prev = await getCache();
  const { jobs, report } = await fetchAll(settings, onProgress);
  // keep earlier postings that are still in the time window (sources only return their newest page)
  const cutoff = Date.now() - (settings.jobs.maxAgeDays || 14) * 86400000;
  const byId = new Map(prev.items.filter((j) => j.postedAt >= cutoff).map((j) => [j.id, j]));
  for (const j of jobs) byId.set(j.id, j);
  const keys = new Set();
  const items = [...byId.values()].sort((a, b) => b.postedAt - a.postedAt).filter((j) => {
    const k = dedupeKey(j);
    if (keys.has(k)) return false;
    keys.add(k);
    return true;
  }).slice(0, 6000);
  const cache = { fetchedAt: Date.now(), items, report };
  await chrome.storage.local.set({ jobs: cache });
  return cache;
}

// viewed / flagged marks
export async function getMarks() {
  const { jobMarks } = await chrome.storage.local.get('jobMarks');
  return jobMarks || { viewed: {}, flagged: {} };
}
export async function mark(kind, id, on = true) {
  const m = await getMarks();
  if (on) m[kind][id] = Date.now();
  else delete m[kind][id];
  await chrome.storage.local.set({ jobMarks: m });
  return m;
}
