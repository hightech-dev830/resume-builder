// OpenAI Chat Completions, called straight from the extension with your API key (Settings → AI).
// Used for form answers while filling, cover letters, and resumes when the resume engine is "OpenAI API".

const URL_ = 'https://api.openai.com/v1/chat/completions';
const reasoning = (model) => /^(gpt-5|o\d)/.test(model);

export async function openaiChat({ apiKey, model, system, user, schema, maxTokens = 6000, effort = 'low', signal }) {
  if (!apiKey) throw new Error('Add your OpenAI API key in Settings → AI.');
  const body = {
    model,
    messages: [...(system ? [{ role: 'system', content: system }] : []), { role: 'user', content: user }],
    max_completion_tokens: maxTokens
  };
  if (schema) body.response_format = { type: 'json_schema', json_schema: { name: 'output', strict: true, schema } };
  if (reasoning(model)) body.reasoning_effort = effort;
  else body.temperature = 0.4;
  let res;
  try {
    res = await fetch(URL_, { method: 'POST', headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  } catch (err) {
    throw new Error('Could not reach OpenAI: ' + err.message);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.error?.message || `HTTP ${res.status}`;
    if (res.status === 401) throw new Error('OpenAI rejected the API key (Settings → AI).');
    if (res.status === 429) throw new Error('OpenAI rate limit or quota reached: ' + msg);
    throw new Error('OpenAI: ' + msg);
  }
  const choice = data.choices?.[0];
  if (choice?.message?.refusal) throw new Error('OpenAI refused: ' + choice.message.refusal);
  const text = choice?.message?.content || '';
  if (!text) throw new Error(choice?.finish_reason === 'length' ? 'OpenAI ran out of tokens before answering; try again.' : 'OpenAI returned an empty answer.');
  return text;
}

export async function openaiJson(opts) {
  const text = await openaiChat(opts);
  try {
    return JSON.parse(text);
  } catch {
    throw new Error('OpenAI did not return valid JSON; try again.');
  }
}
