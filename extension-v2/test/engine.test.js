// Tests for the parts that run without a browser: keyword scoring, the consistency checks,
// the resume text round-trip, the model-answer parser, the DOCX writer and profile import.
// Run: npm test   (inside extension-v2)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { findTech, jdKeywords, atsScore, quickMatch, profileTechSet } from '../lib/ats.js';
import { validate } from '../lib/validate.js';
import { buildPrompt, parseJson, mergeTailored, toText, fromText } from '../lib/resume.js';
import { resumeDocx, zip } from '../lib/docx.js';
import { importProfile, totalYears } from '../lib/profiles.js';
import { renderResumeHtml, listTemplates } from '../lib/templates.js';
import { categoryOf, platformOf, seniorityOf } from '../lib/jobs.js';

const sample = importProfile(readFileSync(new URL('../profiles/sample-profile.json', import.meta.url), 'utf8'));
const JD = `About the role
We build search for clinics.
What we're looking for
- 7+ years of backend development in Python or Go
- Elasticsearch or OpenSearch, relevance ranking
- PostgreSQL, Kafka, AWS, Kubernetes
Nice to have
- Terraform, React`;

// a tailored answer as a model would return it
const answer = {
  company_industry: 'Healthcare search',
  label: 'Senior Backend Engineer, Search',
  summary: 'Senior backend engineer with 12+ years building Python and Go services. Works on Elasticsearch relevance ranking, PostgreSQL and Kafka on AWS and Kubernetes.',
  highlights: [{ name: 'Search', value: 'Elasticsearch, relevance ranking, query parsing' }],
  skills: [{ name: 'Search', keywords: ['Elasticsearch', 'OpenSearch'] }, { name: 'Backend', keywords: ['Python', 'Go', 'FastAPI', 'PostgreSQL', 'Kafka'] }, { name: 'Cloud', keywords: ['AWS', 'Kubernetes', 'Terraform'] }],
  work: [
    { index: 0, position: 'Senior Software Engineer', description: 'Search for a clinic scheduling platform.', highlights: ['Built appointment search on Elasticsearch with relevance ranking tuned on 2M monthly queries.', 'Moved HL7 imports to Kafka consumers in Go.', 'Ran the move of 30 services to Kubernetes on AWS with Terraform.', 'Designed PostgreSQL schemas for clinic calendars.'] },
    { index: 1, position: 'Software Engineer', description: 'Catalog search for a marketplace.', highlights: ['Owned the catalog API in Django with a Solr index.', 'Cached pricing rules in Redis, cutting checkout latency by 35%.', 'Wrote Celery jobs that reconcile payouts nightly.'] },
    { index: 2, position: 'Junior Software Engineer', description: 'Reporting tools.', highlights: ['Built Spring reporting endpoints over MySQL.', 'Kept jQuery dashboards running for retail clients.'] }
  ],
  keywords_matched: ['Elasticsearch']
};

test('dictionary avoids common false matches', () => {
  const names = findTech('the rest of the team, Series C funding, C-level, golang, Java and JavaScript, React Native').map((t) => t.name);
  assert.ok(!names.includes('REST APIs'));
  assert.ok(!names.includes('C'));
  assert.ok(names.includes('Go') && names.includes('Java') && names.includes('JavaScript') && names.includes('React Native'));
  assert.ok(!names.includes('React'), 'React inside "React Native" is not a separate mention');
});

test('JD keywords: requirements weigh more than nice-to-haves', () => {
  const k = jdKeywords(JD, 'Senior Backend Engineer, Search');
  const w = Object.fromEntries(k.tech.map((t) => [t.tech.name, t]));
  assert.ok(w.Elasticsearch.req && w['Apache Kafka'].req);
  assert.ok(!w.Terraform.req && w.Terraform.weight < w['Apache Kafka'].weight);
  assert.equal(k.minYears, 7);
});

test('a tailored resume scores high on ATS and has no high-severity issues', () => {
  const r = mergeTailored(sample.resume, answer);
  const ats = atsScore(r, JD, 'Senior Backend Engineer, Search', totalYears(sample.resume));
  assert.ok(ats.score >= 85, `ATS ${ats.score}`);
  const v = validate(r, sample.resume, { stackMode: 'adjacent' });
  assert.deepEqual(v.issues.filter((x) => x.severity === 'high').map((x) => x.msg), []);
  assert.ok(!r.work[0].stack, 'evidence fields are not printed');
});

test('validator catches invented history', () => {
  const bad = mergeTailored(sample.resume, {
    ...answer,
    summary: 'Engineer with 15+ years of experience and 12 years of Kubernetes.',
    work: [
      { ...answer.work[0], position: 'Principal Engineer' },
      answer.work[1],
      { ...answer.work[2], highlights: ['Built LLM agents with LangChain and RAG on Kubernetes.', 'Built LLM agents with LangChain and RAG on Kubernetes.'] }
    ]
  });
  bad.work[1].name = 'Other Corp';
  const v = validate(bad, sample.resume);
  const msgs = v.issues.map((x) => x.msg).join('\n');
  assert.match(msgs, /Employer changed/);
  assert.match(msgs, /well above the real title/);
  assert.match(msgs, /LangChain .*not available until 2022/);
  assert.match(msgs, /15\+ years/);
  assert.match(msgs, /Kubernetes has only existed|Kubernetes add up/);
  assert.match(msgs, /repeats/);
  assert.ok(v.risk >= 75);
});

test('strict stack mode flags technologies the job did not use', () => {
  const r = mergeTailored(sample.resume, { ...answer, work: [answer.work[0], { ...answer.work[1], highlights: ['Ran Kafka streams for orders.', 'Ran MongoDB for carts.'] }, answer.work[2]] });
  const strict = validate(r, sample.resume, { stackMode: 'strict' }).issues.filter((x) => x.where === 'Bluefin Commerce').map((x) => x.msg).join('\n');
  assert.match(strict, /MongoDB is not in Bluefin/);
  assert.match(strict, /Kafka is not in Bluefin/);
  // adjacent: Kafka is close to RabbitMQ (message queues), MongoDB has nothing related in that stack
  const adj = validate(r, sample.resume, { stackMode: 'adjacent' }).issues.filter((x) => x.where === 'Bluefin Commerce').map((x) => x.msg).join('\n');
  assert.doesNotMatch(adj, /Kafka/);
  assert.match(adj, /MongoDB/);
});

test('editable text round-trips, and facts come from the profile', () => {
  const r = mergeTailored(sample.resume, answer);
  const text = toText(r).replace('Bluefin Commerce |', 'Fake Inc |').replace('2017-03', '2001-01');
  const back = fromText(text, sample.resume);
  assert.equal(back.basics.label, r.basics.label);
  assert.equal(back.basics.summary, r.basics.summary);
  assert.deepEqual(back.skills, r.skills);
  assert.equal(back.work[1].name, 'Bluefin Commerce');
  assert.equal(back.work[1].startDate, '2017-03');
  assert.deepEqual(back.work.map((w) => w.highlights), r.work.map((w) => w.highlights));
  assert.equal(back.work[0].description, r.work[0].description);
});

test('parses JSON out of chat answers', () => {
  assert.equal(parseJson('Sure! Here it is:\n```json\n{"a": 1,}\n```\nGood luck').a, 1);
  assert.equal(parseJson('noise {"label": "x", "work": []} trailing').label, 'x');
  assert.throws(() => parseJson('no json here'));
});

test('prompt carries the facts and the rules', () => {
  const p = buildPrompt({ profile: sample, job: { title: 'Senior Backend Engineer, Search', company: 'Acme', jd: JD }, atsTarget: 95 });
  assert.match(p, /Northwind Health/);
  assert.match(p, /Real stack: Python, FastAPI/);
  assert.match(p, /Must-haves found in the JD: .*Elasticsearch/);
  assert.match(p, /```json/);
});

test('DOCX is a valid ZIP with the Word parts', () => {
  const bytes = resumeDocx(mergeTailored(sample.resume, answer), { font: 'Calibri', size: 10.5, color: '1F3864' });
  assert.equal(String.fromCharCode(bytes[0], bytes[1]), 'PK');
  const text = new TextDecoder().decode(bytes);
  for (const part of ['word/document.xml', 'word/styles.xml', 'word/numbering.xml', '[Content_Types].xml']) assert.ok(text.includes(part));
  assert.ok(text.includes('Northwind Health'));
  assert.equal(zip({ 'a.txt': 'x' }).length > 22, true);
});

test('every template renders', () => {
  const r = mergeTailored(sample.resume, answer);
  for (const t of listTemplates()) assert.match(renderResumeHtml(r, t.id), /Northwind Health/);
});

test('job tagging and match', () => {
  assert.equal(categoryOf('Senior Backend Engineer'), 'Backend');
  assert.equal(categoryOf('Machine Learning Engineer'), 'AI / ML');
  assert.equal(platformOf('https://job-boards.greenhouse.io/acme/jobs/1'), 'Greenhouse');
  assert.equal(platformOf('https://acme.wd5.myworkdayjobs.com/x'), 'Workday');
  assert.equal(seniorityOf('Staff Software Engineer'), 'Staff');
  const set = profileTechSet(sample.resume);
  assert.ok(quickMatch(set, ['Python', 'Elasticsearch', 'Apache Kafka']) === 100);
  assert.ok(quickMatch(set, ['Python', 'OpenSearch', 'Rust']) < 100);
});

test('self-identification follows each profile, never a default person', async () => {
  const { rulePlan } = await import('../lib/rules.js');
  const { setKB } = await import('../lib/examples.js');
  setKB(JSON.parse(readFileSync(new URL('../data/examples.json', import.meta.url), 'utf8')));
  const snap = {
    url: 'https://x', buttons: [], errors: [], text: '',
    fields: [
      { id: 'g', kind: 'select', label: 'Gender', options: ['Male', 'Female', 'Decline to self-identify'] },
      { id: 'v', kind: 'select', label: 'Veteran status', options: ['I am a protected veteran', 'I am not a protected veteran', 'I do not wish to answer'] },
      { id: 'd', kind: 'select', label: 'Disability status', options: ['Yes, I have a disability', 'No, I do not have a disability', "I don't wish to answer"] },
      { id: 'o', kind: 'select', label: 'Sexual orientation', options: ['Heterosexual', 'Gay', 'Prefer not to say'] }
    ]
  };
  const pick = (personal) => Object.fromEntries(rulePlan(snap, { profile: personal }).actions.map((a) => [a.id, a.value]));
  assert.deepEqual(pick({ gender: 'Decline to self-identify', veteran_status: 'Decline', disability_status: 'Prefer not to say' }),
    { g: 'Decline to self-identify', v: 'I do not wish to answer', d: "I don't wish to answer", o: 'Prefer not to say' });
  assert.deepEqual(pick({ gender: 'Female', veteran_status: 'I am a protected veteran', disability_status: 'Yes', sexual_orientation: 'Gay' }),
    { g: 'Female', v: 'I am a protected veteran', d: 'Yes, I have a disability', o: 'Gay' });
  const m = pick({ gender: 'Male', veteran_status: 'I am not a protected veteran', disability_status: 'No', sexual_orientation: 'Heterosexual' });
  assert.equal(m.g, 'Male');
  assert.equal(m.v, 'I am not a protected veteran');
  assert.equal(m.d, 'No, I do not have a disability');
  assert.equal(m.o, 'Heterosexual');
});

test('profile import accepts a bare JSON Resume', () => {
  const p = importProfile({ basics: { name: 'Sam Lee', email: 's@example.com' }, work: [{ name: 'X', startDate: '2020-01' }] });
  assert.equal(p.personal.first_name, 'Sam');
  assert.equal(p.personal.last_name, 'Lee');
  assert.equal(p.name, 'Sam Lee');
});
