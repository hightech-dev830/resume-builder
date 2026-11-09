// Saves the tokens and US$ cost of every AI call (table ai_usage, supabase/migrations/003_ai_usage.sql)
// and summarizes them. Each call is tagged with what it was for (resume, cover letter, page)
// and the bid, through track().
const { AsyncLocalStorage } = require('async_hooks');
const { sb, getSetting } = require('./db');
const { costUsd, totals, summarize, DEFAULT_PRICES } = require('./shared/prices');

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

// All rows (newest first) summarized: today / 7 / 30 days / all time, per day, per model, per task.
async function summary(tz = 'America/Los_Angeles') {
  const { data, error } = await sb().from('ai_usage').select('*').order('created_at', { ascending: false }).limit(100000);
  if (error) {
    if (error.code === 'PGRST205' || /ai_usage/.test(error.message)) return { missingTable: true };
    throw new Error('Supabase: ' + error.message);
  }
  return summarize(data, await prices(), tz);
}

async function forBid(bidId) {
  const { data, error } = await sb().from('ai_usage').select('*').eq('bid_id', bidId);
  if (error) return null;
  return totals(data, await prices());
}

module.exports = { track, record, summary, forBid, prices, resetPrices, DEFAULT_PRICES };
