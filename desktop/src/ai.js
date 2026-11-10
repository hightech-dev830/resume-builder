const Anthropic = require('@anthropic-ai/sdk');
const OpenAIModule = require('openai');
const { ProxyAgent, fetch: undiciFetch } = require('undici');

const OpenAI = OpenAIModule.default || OpenAIModule;

const PROVIDERS = {
  anthropic: { label: 'Claude (Anthropic)', defaultModel: 'claude-opus-5-5' },
  openai: { label: 'OpenAI', defaultModel: 'gpt-5' },
  gemini: { label: 'Google Gemini', defaultModel: 'gemini-2.5-pro' }
};

// Settings keys for each provider's API key and model.
const KEY_SETTING = { anthropic: 'claude_api_key', openai: 'openai_api_key', gemini: 'gemini_api_key' };
const MODEL_SETTING = { anthropic: 'claude_model', openai: 'openai_model', gemini: 'gemini_model' };

// Gemini is called through Google's OpenAI-compatible endpoint, so it shares the OpenAI client.
const GEMINI_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/';

// ---------------- proxy ----------------

// Accepts "host:port:user:pass", "host:port", or a full http(s):// URL.
function parseProxy(raw) {
  const s = String(raw || '').trim();
  if (!s) return null;
  if (/^https?:\/\//i.test(s)) return new URL(s).toString();
  const [host, port, user, ...rest] = s.split(':');
  if (!host || !port || !/^\d+$/.test(port)) throw new Error('Proxy must look like host:port:user:pass or http://user:pass@host:port');
  const pass = rest.join(':');
  const auth = user ? `${encodeURIComponent(user)}:${encodeURIComponent(pass)}@` : '';
  return `http://${auth}${host}:${port}`;
}

let proxyCache = { raw: null, fetch: null };

function proxiedFetch(rawProxy) {
  if (proxyCache.raw === rawProxy) return proxyCache.fetch;
  const url = parseProxy(rawProxy);
  const dispatcher = url ? new ProxyAgent(url) : null;
  const fn = dispatcher ? (input, init = {}) => undiciFetch(input, { ...init, dispatcher }) : null;
  proxyCache = { raw: rawProxy, fetch: fn };
  return fn;
}

// ---------------- default prompts (editable in Settings) ----------------

const DEFAULT_RESUME_PROMPT = `You are a senior technical recruiter and resume writer. Rebuild the candidate's resume around THIS job description so a hiring manager for this exact role sees a direct match on the first read.

The base profile is the factual skeleton: names of employers, employment dates, locations, education and contact details stay exactly as given. Everything else (headline, summary, competencies, skills, job titles within reason, job descriptions and every bullet) is rewritten for this job. Do not copy the base bullets; write fresh content for this role.

1. Read the JD first. Identify the must-have languages, frameworks, databases, cloud and DevOps tools, architecture patterns, domain, seniority and the main responsibilities. Weight the requirements by how prominently the JD states them.

2. Headline: use the JD's job title (or the closest matching title family).

3. Summary: 3-4 sentences. Open with total years of experience (count from the earliest start date) and the JD's core stack. Then the kind of systems built that match this role's domain and scale, then one sentence on how the candidate works (ownership, collaboration, quality) in the JD's language. Optional short second paragraph only if it adds a distinct point.

4. Core competencies: 4 lines (label + comma-separated details) that mirror the JD's requirement groups, in the JD's priority order.

5. Skills: group names and order should follow the JD. Put every JD-required technology first in its group. Keep useful breadth, but drop items that only add noise for this role.

6. Experience (most important):
   - Spread the JD's required skills across the jobs realistically. The two most recent jobs carry most of the JD's stack and responsibilities; older jobs show the foundations and earlier versions of the same skills.
   - Every technology must be plausible for that company's domain and for its dates (for example, no LLM, RAG or agent work before 2022; use the tools and versions that existed at the time).
   - Rewrite each job's one-line description so it frames the work in terms this JD cares about.
   - Bullets: action verb + what was built or changed + the key technologies + a concrete result or scope. Use specific, modest, believable numbers (users, requests per second, data volume, latency, cost, team size) and vary them; never use round "100%" style claims.
   - 5-6 bullets for each of the two most recent jobs, 3-4 for older jobs. Each bullet 1-2 lines. Vary sentence openings and verbs; avoid repeating the same verb within a job.
   - Cover the JD's responsibilities too (design reviews, mentoring, on-call, stakeholder work, testing, CI/CD) where the JD asks for them.

7. Keep it credible: every bullet should be something the candidate could explain in a technical interview. Do not add certifications, degrees or employers.

8. Style: no first person, no em dashes, American English. Avoid filler words such as "leveraged", "spearheaded", "synergy", "cutting-edge", "passionate", "robust". Plain, specific, confident.

9. Follow any extra instructions from the user; they override these rules.`;

const DEFAULT_COVER_PROMPT = `Write a short, simple and realistic cover letter for a senior software engineer applying to this job.

- 150-230 words, 3 short paragraphs. Body text only: no letterhead, date, address block or subject line.
- Start with "Dear Hiring Manager," (or "Dear <Company> team," when the company name is known). End with "Best regards," and the candidate's full name on the next line.
- Paragraph 1: the role being applied for and one specific reason it fits, taken from the job description (product, domain or tech stack).
- Paragraph 2: two concrete examples from the resume that match the job's main requirements. Name the technologies and what was built. Keep numbers modest and only use ones that appear in the resume.
- Paragraph 3: one or two sentences offering a call to talk further.
- Write like a real engineer writes an email: plain words, short sentences, no hype. No clichés ("I am writing to express my interest", "passionate", "perfect fit", "thrilled", "esteemed"), no em dashes, no bullet points.
- Output only the letter text.`;

// ---------------- structured output schema ----------------

// The model writes only what changes per job; employers, dates, locations,
// contact details and education always come from the base profile.
const TAILOR_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['label', 'summary', 'highlights', 'skills', 'work', 'keywords_matched'],
  properties: {
    label: { type: 'string', description: 'Headline / target role title shown under the name' },
    summary: { type: 'string', description: 'Profile summary; separate paragraphs with a blank line' },
    highlights: {
      type: 'array',
      description: 'Core-competency lines shown in the profile section',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['name', 'value'],
        properties: { name: { type: 'string' }, value: { type: 'string' } }
      }
    },
    skills: {
      type: 'array',
      description: 'Skill groups, most relevant to the job first',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['name', 'keywords'],
        properties: { name: { type: 'string' }, keywords: { type: 'array', items: { type: 'string' } } }
      }
    },
    work: {
      type: 'array',
      description: 'One entry per base-profile job, same order, same count',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['index', 'position', 'description', 'highlights'],
        properties: {
          index: { type: 'integer', description: '0-based index of the job in the base profile' },
          position: { type: 'string' },
          description: { type: 'string' },
          highlights: { type: 'array', items: { type: 'string' } }
        }
      }
    },
    keywords_matched: {
      type: 'array',
      description: 'Important JD keywords now covered by the resume',
      items: { type: 'string' }
    }
  }
};

// ---------------- usage ----------------

// Every completed AI call reports its token counts here (the app saves them with the cost).
let usageListener = null;
function onUsage(fn) {
  usageListener = fn;
}
function reportUsage(cfg, model, usage) {
  if (!usageListener) return;
  try {
    usageListener({ provider: cfg.provider, model, ...usage });
  } catch (err) {
    console.warn('usage listener failed:', err.message);
  }
}

// ---------------- providers ----------------

function requireKey(cfg) {
  if (!cfg.apiKey) throw new Error(`${PROVIDERS[cfg.provider]?.label || 'AI'} API key is not set. Add it in Settings.`);
}

function anthropicClient(cfg) {
  requireKey(cfg);
  const fetch = proxiedFetch(cfg.proxy);
  return new Anthropic({ apiKey: cfg.apiKey, ...(fetch ? { fetch } : {}) });
}

function openaiClient(cfg) {
  requireKey(cfg);
  const fetch = proxiedFetch(cfg.proxy);
  return new OpenAI({ apiKey: cfg.apiKey, ...(cfg.provider === 'gemini' ? { baseURL: GEMINI_BASE_URL } : {}), ...(fetch ? { fetch } : {}) });
}

// Claude models that accept output_config.effort, and the ones that take server-side fallbacks.
const CLAUDE_EFFORT = /claude-(opus-(4-[5-8]|5)|sonnet-(4-6|5)|fable|mythos|haiku-5)/;
const CLAUDE_FALLBACK = /^claude-(fable-5-1|mythos-5-1|opus-5-5|opus-5|sonnet-5-5)$/;

async function completeAnthropic(cfg, { system, user, schema, maxTokens, effort }) {
  const client = anthropicClient(cfg);
  const model = cfg.model || PROVIDERS.anthropic.defaultModel;
  const output_config = {};
  if (CLAUDE_EFFORT.test(model)) output_config.effort = effort;
  if (schema) output_config.format = { type: 'json_schema', schema };
  const params = {
    model,
    max_tokens: maxTokens,
    system,
    messages: [{ role: 'user', content: user }],
    ...(Object.keys(output_config).length ? { output_config } : {})
  };
  const stream = CLAUDE_FALLBACK.test(model)
    ? client.beta.messages.stream({ ...params, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' })
    : client.messages.stream(params);
  const message = await stream.finalMessage();
  const u = message.usage || {};
  reportUsage(cfg, message.model || model, {
    input: (u.input_tokens || 0) + (u.cache_creation_input_tokens || 0),
    cached: u.cache_read_input_tokens || 0,
    output: u.output_tokens || 0
  });
  if (message.stop_reason === 'refusal') throw new Error('Claude declined this request. Try rephrasing the job details or the prompt.');
  if (message.stop_reason === 'max_tokens') throw new Error('The response was cut off (max tokens). Try again.');
  return message.content.filter((b) => b.type === 'text').map((b) => b.text).join('');
}

// OpenAI reasoning models (gpt-5 and later, o-series) take reasoning_effort; without it they think at
// "medium" on every call. Older chat models (gpt-4o, gpt-4.1) reject the parameter.
const OPENAI_REASONING = /^(gpt-[5-9]|o\d)/i;

async function completeOpenAI(cfg, { system, user, schema, maxTokens, effort }) {
  const client = openaiClient(cfg);
  const gemini = cfg.provider === 'gemini';
  const model = cfg.model || PROVIDERS[cfg.provider]?.defaultModel || PROVIDERS.openai.defaultModel;
  const body = {
    model,
    ...(gemini ? { max_tokens: maxTokens } : { max_completion_tokens: maxTokens }),
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: user }
    ],
    ...(schema ? { response_format: { type: 'json_schema', json_schema: { name: 'structured_output', schema, strict: true } } } : {})
  };
  if (!gemini && effort && OPENAI_REASONING.test(model)) body.reasoning_effort = effort;
  let res;
  try {
    res = await client.chat.completions.create(body);
  } catch (err) {
    // a model that does not know reasoning_effort (or this value of it): ask again without it
    if (!body.reasoning_effort || !/reasoning/i.test(err.message || '')) throw err;
    delete body.reasoning_effort;
    res = await client.chat.completions.create(body);
  }
  const u = res.usage || {};
  const cached = u.prompt_tokens_details?.cached_tokens || 0;
  reportUsage(cfg, res.model || cfg.model, { input: (u.prompt_tokens || 0) - cached, cached, output: u.completion_tokens || 0 });
  const choice = res.choices[0];
  if (choice.message.refusal) throw new Error(`${PROVIDERS[cfg.provider]?.label || 'AI'} declined this request: ${choice.message.refusal}`);
  if (choice.finish_reason === 'length') throw new Error('The response was cut off (max tokens). Try again.');
  return choice.message.content || '';
}

// Turns SDK API errors into one readable line, e.g. "Claude (Anthropic): Your credit balance is too low…".
function friendlyError(cfg, err) {
  const label = PROVIDERS[cfg.provider]?.label || 'AI';
  if (err instanceof Anthropic.APIError || err instanceof OpenAI.APIError) {
    const msg = err.error?.error?.message || err.error?.message || err.message;
    if (err instanceof Anthropic.APIConnectionError || err instanceof OpenAI.APIConnectionError) {
      return new Error(`${label}: could not connect (${err.cause?.message || 'network error'}). Check the proxy in Settings.`);
    }
    return new Error(`${label}${err.status ? ` (HTTP ${err.status})` : ''}: ${msg}`);
  }
  return err;
}

async function complete(cfg, req) {
  try {
    if (cfg.provider === 'openai' || cfg.provider === 'gemini') return await completeOpenAI(cfg, req);
    return await completeAnthropic(cfg, req);
  } catch (err) {
    throw friendlyError(cfg, err);
  }
}

// ---------------- tasks ----------------

function jobBlock(job) {
  return [
    `Company: ${job.company || '(not given)'}`,
    `Role: ${job.role || '(not given)'}`,
    job.location ? `Location: ${job.location}` : '',
    job.salary ? `Salary: ${job.salary}` : '',
    job.job_link ? `Job link: ${job.job_link}` : '',
    '',
    '<job_description>',
    job.jd || '(no job description provided)',
    '</job_description>'
  ].filter((l) => l !== '').join('\n');
}

async function tailorResume({ cfg, prompt, base, job }) {
  const text = await complete(cfg, {
    system: prompt || DEFAULT_RESUME_PROMPT,
    schema: TAILOR_SCHEMA,
    maxTokens: 32000,
    effort: 'high',
    user: `<base_profile>\n${JSON.stringify(base, null, 2)}\n</base_profile>\n\n${jobBlock(job)}${
      job.instructions ? `\n\n<extra_instructions>\n${job.instructions}\n</extra_instructions>` : ''
    }\n\nReturn the tailored resume content as JSON.`
  });
  let tailored;
  try {
    tailored = JSON.parse(text);
  } catch {
    throw new Error('The model did not return valid JSON. Try again or pick another model.');
  }
  return { resume: mergeTailored(base, tailored), keywords: tailored.keywords_matched || [] };
}

function mergeTailored(base, t) {
  const resume = JSON.parse(JSON.stringify(base));
  resume.basics.label = t.label || resume.basics.label;
  resume.basics.summary = t.summary || resume.basics.summary;
  if (Array.isArray(t.highlights) && t.highlights.length) resume.basics.highlights = t.highlights;
  if (Array.isArray(t.skills) && t.skills.length) resume.skills = t.skills;
  for (const w of t.work || []) {
    const job = resume.work[w.index];
    if (!job) continue;
    job.position = w.position || job.position;
    job.description = w.description ?? job.description;
    if (Array.isArray(w.highlights) && w.highlights.length) job.highlights = w.highlights;
  }
  return resume;
}

async function generateCoverLetter({ cfg, prompt, resume, job, instructions }) {
  const text = await complete(cfg, {
    system: prompt || DEFAULT_COVER_PROMPT,
    maxTokens: 16000,
    effort: 'medium',
    user: `<resume>\n${JSON.stringify(resume, null, 2)}\n</resume>\n\n${jobBlock(job)}${
      instructions ? `\n\n<extra_instructions>\n${instructions}\n</extra_instructions>` : ''
    }\n\nWrite the cover letter. Output only the letter text.`
  });
  return text.trim();
}

// ---------------- settings helpers ----------------

const OPENAI_CHAT_MODEL = /^(gpt-|o\d|chatgpt-)/;
const OPENAI_NOT_CHAT = /(audio|realtime|tts|transcribe|image|search|embedding|moderation|dall-e|whisper|instruct)/;

async function listModels(cfg) {
  try {
    return await fetchModels(cfg);
  } catch (err) {
    throw friendlyError(cfg, err);
  }
}

const GEMINI_NOT_CHAT = /(embedding|aqa|imagen|veo|tts|image|audio|live|native|learnlm|gemma)/;

async function fetchModels(cfg) {
  if (cfg.provider === 'gemini') {
    const client = openaiClient(cfg);
    const out = [];
    for await (const m of client.models.list()) {
      const id = String(m.id).replace(/^models\//, '');
      if (/^gemini-/.test(id) && !GEMINI_NOT_CHAT.test(id)) out.push({ id, name: m.display_name || id });
    }
    return out.sort((a, b) => b.id.localeCompare(a.id));
  }
  if (cfg.provider === 'openai') {
    const client = openaiClient(cfg);
    const out = [];
    for await (const m of client.models.list()) {
      if (OPENAI_CHAT_MODEL.test(m.id) && !OPENAI_NOT_CHAT.test(m.id)) out.push({ id: m.id, name: m.id, created: m.created });
    }
    return out.sort((a, b) => (b.created || 0) - (a.created || 0));
  }
  const client = anthropicClient(cfg);
  const out = [];
  for await (const m of client.models.list()) out.push({ id: m.id, name: m.display_name || m.id });
  return out;
}

async function testKey(cfg) {
  return (await complete(cfg, { system: 'You are a test endpoint.', user: 'Reply with the single word: ok', maxTokens: 2000, effort: 'low' })).trim();
}

// Checks the proxy itself: any HTTP answer from the provider means it works.
async function testProxy(proxy, provider = 'anthropic') {
  const fetch = proxiedFetch(proxy);
  if (!fetch) throw new Error('No proxy set.');
  const url = { openai: 'https://api.openai.com/v1/models', gemini: GEMINI_BASE_URL + 'models' }[provider] || 'https://api.anthropic.com/v1/models';
  const started = Date.now();
  try {
    const res = await fetch(url, { method: 'GET', signal: AbortSignal.timeout(15000) });
    return { status: res.status, ms: Date.now() - started, host: new URL(url).host };
  } catch (err) {
    throw new Error(err.cause?.message || err.message);
  }
}

module.exports = {
  PROVIDERS, KEY_SETTING, MODEL_SETTING, DEFAULT_RESUME_PROMPT, DEFAULT_COVER_PROMPT,
  complete, jobBlock, onUsage, tailorResume, generateCoverLetter, listModels, testKey, testProxy, parseProxy
};
