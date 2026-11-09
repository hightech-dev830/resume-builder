// Saves the tokens and US$ cost of every AI call (table ai_usage, supabase/migrations/003_ai_usage.sql)
// and summarizes them. Each call is tagged with what it was for (resume, cover letter, page)
// and the bid, through track().
const { AsyncLocalStorage } = require('async_hooks');
const { sb, getSetting } = require('./db');
const { costUsd, DEFAULT_PRICES } = require('./shared/prices');

const ctx = new AsyncLocalStorage();
let tableMissing = false;
let priceCache = { t: 0, prices: {} };

// Runs fn with a task label and bid id attached to any AI call it makes.
const track = (info, fn) => ctx.run(info, fn);

async function prices() {
  if (Date.now() - priceCache.t > 60000) {
    let saved = {};
    try {
      saved = JSON.parse((await getSetting('ai_prices', '')) || '{}');
    } catch {}
    priceCache = { t: Date.now(), prices: saved };
  }
  return priceCache.prices;
}

function resetPrices() {
  priceCache = { t: 0, prices: {} };
}

async function record(u) {
  if (tableMissing) return;
  const info = ctx.getStore() || {};
  const row = {
    provider: u.provider, model: u.model || '', task: info.task || '', bid_id: info.bidId || null,
    input_tokens: u.input || 0, cached_tokens: u.cached || 0, output_tokens: u.output || 0,
    cost_usd: costUsd(u.model, u, await prices())
  };
  const { error } = await sb().from('ai_usage').insert(row);
  if (error) {
    if (error.code === 'PGRST205' || /ai_usage/.test(error.message)) {
      tableMissing = true;
      console.warn('AI usage is not saved: run supabase/migrations/003_ai_usage.sql in Supabase.');
    } else console.warn('Could not save AI usage:', error.message);
  }
}

const tokens = (r) => (r.input_tokens || 0) + (r.cached_tokens || 0) + (r.output_tokens || 0);

// Cost of a row: saved at the time, or worked out now if the price was added later.
function rowCost(r, p) {
  if (r.cost_usd != null) return Number(r.cost_usd);
  return costUsd(r.model, { input: r.input_tokens, cached: r.cached_tokens, output: r.output_tokens }, p);
}

function totals(rows, p) {
  const out = { calls: rows.length, input: 0, cached: 0, output: 0, tokens: 0, cost: 0, unpriced: 0 };
  for (const r of rows) {
    out.input += r.input_tokens || 0;
    out.cached += r.cached_tokens || 0;
    out.output += r.output_tokens || 0;
    out.tokens += tokens(r);
    const c = rowCost(r, p);
    if (c == null) out.unpriced++;
    else out.cost += c;
  }
  return out;
}

// Today / last 7 days / last 30 days, plus a per-model breakdown for the 30 days.
async function summary(tz = 'America/Los_Angeles') {
  const since = new Date(Date.now() - 30 * 86400000).toISOString();
  const { data, error } = await sb().from('ai_usage').select('*').gte('created_at', since).order('created_at', { ascending: false }).limit(50000);
  if (error) {
    if (error.code === 'PGRST205' || /ai_usage/.test(error.message)) return { missingTable: true };
    throw new Error('Supabase: ' + error.message);
  }
  const p = await prices();
  const day = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(d));
  const today = day(Date.now());
  const weekAgo = Date.now() - 7 * 86400000;
  const byModel = {};
  for (const r of data) (byModel[`${r.provider} / ${r.model}`] ||= []).push(r);
  return {
    today: totals(data.filter((r) => day(r.created_at) === today), p),
    week: totals(data.filter((r) => new Date(r.created_at) >= weekAgo), p),
    month: totals(data, p),
    models: Object.entries(byModel).map(([model, rows]) => ({ model, ...totals(rows, p) })).sort((a, b) => b.cost - a.cost)
  };
}

async function forBid(bidId) {
  const { data, error } = await sb().from('ai_usage').select('*').eq('bid_id', bidId);
  if (error) return null;
  return totals(data, await prices());
}

module.exports = { track, record, summary, forBid, prices, resetPrices, DEFAULT_PRICES };
