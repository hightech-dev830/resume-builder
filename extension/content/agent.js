// Injected into every frame of an application tab. Reads the page into a compact snapshot
// (fields, buttons, text) for the AI planner, and carries out the planner's actions with
// events that React / Vue / Angular forms accept. Safe to inject more than once.
(() => {
  if (window.__abAgent) return;

  const SUCCESS_RE = /(thank(s| you)[^.]{0,40}(appl|submi|interest)|application (was |has been )?(successfully )?(submitted|received|complete)|we('ve| have) received your application|successfully (applied|submitted)|you('ve| have) (successfully )?applied|your application is (complete|on its way|in)|congratulations[^.]{0,40}appl)/i;
  const ALREADY_RE = /(already applied|already submitted an application|you have applied to this (job|position) before)/i;
  const COOKIE_WORDS = /cookie|consent|privacy|gdpr|tracking/i;
  const COOKIE_BUTTON = /^(accept|accept all|accept all cookies|accept cookies|agree|i agree|agree and proceed|agree & proceed|allow|allow all|allow all cookies|allow cookies|ok|okay|got it|i understand|yes,? i agree|accept and continue|accept & continue|continue)$/i;
  const COOKIE_IDS = ['#onetrust-accept-btn-handler', '#truste-consent-button', '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    '#CybotCookiebotDialogBodyButtonAccept', '.cc-allow', '.cookie-accept', '[data-testid="cookie-accept"]', '#accept-cookies', '#cookie-accept',
    'button[data-cookiebanner="accept_button"]', '#hs-eu-confirmation-button', '.osano-cm-accept-all', '#didomi-notice-agree-button'];

  let seq = 0;

  // ---------- DOM helpers (walk open shadow roots too) ----------

  function deepAll(selector, root = document) {
    const out = [...root.querySelectorAll(selector)];
    for (const el of root.querySelectorAll('*')) if (el.shadowRoot) out.push(...deepAll(selector, el.shadowRoot));
    return out;
  }

  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const textOf = (el) => clean(el ? el.innerText || el.textContent : '');

  function visible(el) {
    if (!el || !el.isConnected) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && Number(s.opacity) > 0.05;
  }

  const idOf = (el) => el.dataset.abid || (el.dataset.abid = String(++seq));
  const byId = (id) => deepAll(`[data-abid="${CSS.escape(String(id))}"]`)[0] || null;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  function labelFor(el) {
    const parts = [];
    if (el.labels?.length) parts.push(...[...el.labels].map(textOf));
    const by = el.getAttribute('aria-labelledby');
    if (by) parts.push(by.split(/\s+/).map((id) => textOf(el.getRootNode().getElementById?.(id) || document.getElementById(id))).join(' '));
    if (el.getAttribute('aria-label')) parts.push(el.getAttribute('aria-label'));
    if (!clean(parts.join(' '))) {
      let node = el;
      for (let i = 0; i < 5 && node; i++) {
        node = node.parentElement;
        if (!node) break;
        const lab = node.querySelector(':scope > label, :scope > legend, :scope > [class*="label" i], :scope > [class*="question" i], :scope > [class*="title" i]');
        if (lab && !lab.contains(el) && textOf(lab)) {
          parts.push(textOf(lab));
          break;
        }
      }
    }
    if (!clean(parts.join(' '))) parts.push(precedingText(el));
    return clean(parts.join(' ')).slice(0, 250);
  }

  // Closest text that comes before the element inside its form block.
  function precedingText(el) {
    let node = el;
    for (let i = 0; i < 4 && node; i++) {
      let sib = node.previousElementSibling;
      while (sib) {
        const t = textOf(sib);
        if (t && t.length < 300) return t;
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
    }
    return '';
  }

  // The question for a radio / checkbox group: legend, radiogroup label, or the block's heading text.
  function groupQuestion(inputs) {
    const first = inputs[0];
    const fs = first.closest('fieldset');
    if (fs?.querySelector('legend')) return textOf(fs.querySelector('legend'));
    const rg = first.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      const l = rg.getAttribute('aria-label') || (rg.getAttribute('aria-labelledby') && textOf(document.getElementById(rg.getAttribute('aria-labelledby'))));
      if (l) return clean(l);
    }
    // smallest ancestor holding the whole group, then the text that is not an option label
    let box = first.parentElement;
    while (box && !inputs.every((i) => box.contains(i))) box = box.parentElement;
    for (let i = 0; i < 3 && box; i++) {
      const optionText = new Set(inputs.map((x) => optionLabel(x)));
      const own = clean([...box.childNodes].filter((n) => n.nodeType === 3 || (n.nodeType === 1 && !inputs.some((x) => n.contains(x)))).map((n) => n.textContent).join(' '));
      if (own && !optionText.has(own)) return own.slice(0, 300);
      const prev = precedingText(box);
      if (prev) return prev.slice(0, 300);
      box = box.parentElement;
    }
    return labelFor(first);
  }

  function optionLabel(input) {
    if (input.labels?.length) return textOf(input.labels[0]);
    if (input.getAttribute('aria-label')) return clean(input.getAttribute('aria-label'));
    const wrap = input.closest('label');
    if (wrap) return textOf(wrap);
    const next = input.nextElementSibling;
    return next ? textOf(next) : clean(input.value);
  }

  function fieldError(el) {
    const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
    for (const id of ids) {
      const t = textOf(document.getElementById(id));
      if (t && /required|invalid|error|please|must|enter/i.test(t)) return t.slice(0, 160);
    }
    if (el.getAttribute('aria-invalid') === 'true') return 'invalid';
    const box = el.closest('div, fieldset, li');
    const err = box?.querySelector('[class*="error" i]:not(input), [role="alert"]');
    const t = err && visible(err) ? textOf(err) : '';
    return t && t.length < 160 ? t : '';
  }

  // ---------- snapshot ----------

  const SKIP_INPUT = new Set(['hidden', 'submit', 'button', 'image', 'reset', 'search']);

  function isCombobox(el) {
    return el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox' || !!el.getAttribute('aria-autocomplete')
      || !!el.closest('[data-automation-id*="multiselect" i], [data-uxi-widget-type="selectinput"], [data-automation-id="searchBox"]');
  }

  function snapshot() {
    const fields = [];
    const groups = new Map();

    for (const el of deepAll('input, textarea, select, [contenteditable="true"], [role="combobox"]:not(input), button[aria-haspopup="listbox"]')) {
      const type = (el.getAttribute('type') || el.type || '').toLowerCase();
      if (el.tagName === 'INPUT' && SKIP_INPUT.has(type)) continue;
      const isFile = type === 'file';
      if (!isFile && !visible(el) && !(type === 'radio' || type === 'checkbox')) continue;
      if (el.disabled) continue;

      if (type === 'radio' || type === 'checkbox') {
        const name = el.name || '';
        // single checkbox (consent etc.) vs groups
        const key = name ? `${type}:${name}` : null;
        if (key) {
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(el);
          continue;
        }
        if (!visible(el) && !visible(el.closest('label'))) continue;
        fields.push({ id: idOf(el), kind: 'checkbox', label: labelFor(el) || optionLabel(el), required: el.required, checked: el.checked, error: fieldError(el) });
        continue;
      }

      const f = { id: idOf(el), label: labelFor(el), name: el.name || el.id || '', placeholder: el.placeholder || '', required: el.required || el.getAttribute('aria-required') === 'true' };
      if (isFile) {
        f.kind = 'file';
        f.accept = el.accept || '';
        f.hidden = !visible(el);
        f.value = el.files?.length ? [...el.files].map((x) => x.name).join(', ') : '';
        if (!f.label) f.label = textOf(el.closest('div, section, fieldset'))?.slice(0, 160);
      } else if (el.tagName === 'SELECT') {
        f.kind = 'select';
        f.options = [...el.options].map((o) => clean(o.text)).filter(Boolean);
        f.value = el.selectedIndex > 0 || (el.selectedIndex === 0 && el.options[0]?.value) ? clean(el.options[el.selectedIndex]?.text) : '';
      } else if (el.tagName === 'TEXTAREA') {
        f.kind = 'textarea';
        f.value = clean(el.value).slice(0, 200);
      } else if (el.isContentEditable) {
        f.kind = 'richtext';
        f.value = textOf(el).slice(0, 200);
      } else if (isCombobox(el)) {
        f.kind = 'combobox';
        // the dropdown's real choices, read by probeOptions()
        const known = el.dataset.abopts ? JSON.parse(el.dataset.abopts) : [];
        if (known.length) f.options = known;
        const shown = el.tagName === 'INPUT' ? el.value : textOf(el);
        const container = el.closest('[class*="select" i], [class*="dropdown" i]');
        const single = container?.querySelector('[class*="singleValue" i], [class*="single-value" i], [class*="selected" i]');
        f.value = clean(shown || (single ? textOf(single) : '')).slice(0, 120);
      } else {
        f.kind = type || 'text';
        f.value = type === 'password' ? (el.value ? '(filled)' : '') : clean(el.value).slice(0, 200);
      }
      const err = fieldError(el);
      if (err) f.error = err;
      fields.push(f);
    }

    for (const [key, inputs] of groups) {
      const shown = inputs.filter((i) => visible(i) || visible(i.closest('label')) || visible(i.parentElement));
      if (!shown.length) continue;
      const kind = key.startsWith('radio') ? 'radio group' : inputs.length === 1 ? 'checkbox' : 'checkbox group';
      if (kind === 'checkbox') {
        const el = inputs[0];
        fields.push({ id: idOf(el), kind, label: labelFor(el) || optionLabel(el), required: el.required, checked: el.checked, error: fieldError(el) });
        continue;
      }
      const id = idOf(inputs[0]);
      inputs.forEach((i) => (i.dataset.abgroup = id));
      fields.push({
        id, kind, question: groupQuestion(inputs), label: '',
        options: inputs.map(optionLabel),
        value: inputs.filter((i) => i.checked).map(optionLabel).join(' | '),
        required: inputs.some((i) => i.required), error: fieldError(inputs[0])
      });
    }

    // Yes/No questions drawn as buttons, with no input behind them (Ashby: "Are you authorized…?" [Yes] [No]):
    // listed as a radio group on the buttons' row; "select" clicks the matching button
    const btnRows = new Set();
    for (const b of deepAll('button, [role="button"], [role="radio"]')) {
      if (!/^(yes|no)$/i.test(textOf(b)) || !visible(b) || b.closest('[role="dialog"] footer')) continue;
      const row = b.parentElement;
      if (!row || btnRows.has(row)) continue;
      const opts = [...row.querySelectorAll(':scope > button, :scope > [role="button"], :scope > [role="radio"]')].filter(visible);
      const texts = opts.map(textOf);
      if (opts.length < 2 || opts.length > 4 || !texts.some((t) => /^yes$/i.test(t)) || !texts.some((t) => /^no$/i.test(t))) continue;
      btnRows.add(row);
      // the question: a label / legend / heading in the field's box, else the text before the row
      let question = '';
      for (let box = row.parentElement, i = 0; box && i < 4 && !question; box = box.parentElement, i++) {
        const lab = [...box.querySelectorAll('label, legend, [class*="title" i], [class*="question" i], h3, h4, p')]
          .find((l) => !l.contains(row) && !row.contains(l) && textOf(l).length > 3 && !/^(yes|no)$/i.test(textOf(l)));
        if (lab) question = textOf(lab).slice(0, 300);
      }
      if (!question) question = precedingText(row).slice(0, 300);
      const on = (o) => o.getAttribute('aria-pressed') === 'true' || o.getAttribute('aria-checked') === 'true'
        || /(^|[\s_-])(active|selected|checked|isSelected)/i.test(typeof o.className === 'string' ? o.className : '');
      const id = idOf(row);
      row.dataset.abbtns = '1';
      fields.push({
        id, kind: 'radio group', question, label: '', options: texts,
        value: opts.filter(on).map(textOf).join(' | '),
        required: /[*✱]|\(required\)/.test(question), error: fieldError(row)
      });
    }

    const buttons = [];
    const seen = new Set();
    // dropdown buttons ("Apply Now ▾" opens a menu with "Apply Now" / "Apply with LinkedIn"):
    // the toggle is marked `menu`, the entries of the open menu `inMenu`
    const MENU_SEL = '[role="menu"], [role="menuitem"], [class*="dropdown-menu" i], [class*="dropdown-content" i], [class*="dropdown-list" i], [class*="popover" i], [class*="menu-list" i]';
    const isToggle = (el) => /^(true|menu)$/i.test(el.getAttribute('aria-haspopup') || '') || el.hasAttribute('aria-expanded')
      || /[▾▼⌄˅]\s*$/.test(el.innerText || '') || !!el.querySelector('[class*="caret" i], [class*="chevron" i], [class*="arrow-down" i]')
      || /dropdown-toggle/i.test(el.className?.baseVal ?? el.className ?? '');
    for (const el of deepAll('button, input[type="submit"], input[type="button"], [role="button"], a[href], [role="link"], [role="tab"], [role="menuitem"], [role="menu"] a, [class*="dropdown-menu" i] a, [class*="dropdown-menu" i] li')) {
      if (!visible(el) || seen.has(el)) continue;
      if (el.getAttribute('aria-haspopup') === 'listbox') continue;
      if (el.parentElement && btnRows.has(el.parentElement)) continue; // a Yes/No answer, listed as a field
      const text = clean(el.innerText || el.value || el.getAttribute('aria-label') || el.title).slice(0, 80);
      if (!text) continue;
      const inMenu = !!el.closest(MENU_SEL);
      if (el.tagName === 'LI' && el.querySelector('a, button')) continue; // its link/button is listed itself
      const isLink = el.tagName === 'A' && !el.getAttribute('role');
      // plain links are only interesting when they look like actions
      if (isLink && !/apply|application|next|continue|submit|sign ?in|log ?in|register|sign ?up|create|account|accept|agree|start|review|upload|resume|skip|proceed|manual|yes|no thanks|later|close/i.test(text)) continue;
      seen.add(el);
      buttons.push({
        id: idOf(el), kind: isLink ? 'link' : 'button', text, href: isLink ? String(el.getAttribute('href') || '').slice(0, 80) : '',
        disabled: el.disabled || el.getAttribute('aria-disabled') === 'true',
        ...(inMenu ? { inMenu: true } : !inMenu && isToggle(el) ? { menu: true } : {})
      });
      if (buttons.length >= 90) break;
    }

    const errors = deepAll('[role="alert"], [aria-live="assertive"], [class*="error" i]:not(input):not(select), [class*="invalid" i]:not(input)')
      .filter(visible)
      // an error summary ("Your form needs corrections" + one line per missing field) is read line by line
      .flatMap((el) => {
        const items = [...el.querySelectorAll('li')].filter(visible).map(textOf).filter(Boolean);
        return items.length ? items : [textOf(el)];
      })
      .filter((t) => t && t.length < 400);

    const text = textOf(document.body).slice(0, 9000);
    return {
      url: location.href,
      title: document.title,
      loading: isLoading(),
      steps: wizardSteps(),
      top: window === window.top,
      area: Math.round(window.innerWidth * window.innerHeight),
      fields: fields.slice(0, 160),
      buttons,
      errors: [...new Set(errors)].slice(0, 15),
      text,
      captcha: detectCaptcha(),
      success: SUCCESS_RE.test(text) && !fields.some((f) => f.required && !f.value && f.kind !== 'file'),
      already: ALREADY_RE.test(text)
    };
  }

  // A spinner or busy region means the page is still rendering.
  function isLoading() {
    const busy = deepAll('[aria-busy="true"], [data-automation-id="loadingSpinner"], [class*="spinner" i], [class*="loading" i], [role="progressbar"]')
      .filter((e) => visible(e) && e.getBoundingClientRect().width < window.innerWidth * 0.9);
    return busy.length > 0 && !/thank you|submitted/i.test(textOf(document.body).slice(0, 2000));
  }

  // "Autofill with Resume > My Information (current) > …" from a wizard progress bar, if any.
  function wizardSteps() {
    const bar = deepAll('[data-automation-id="progressBar"], [class*="progress" i] ol, [class*="stepper" i], [class*="steps" i] ol, nav[aria-label*="step" i] ol')
      .find((b) => visible(b) && b.children.length >= 3 && b.children.length <= 12);
    if (!bar) return '';
    return [...bar.children].map((c) => {
      const t = textOf(c);
      const cur = c.getAttribute('aria-current') || /active|current|selected/i.test(c.className) || c.querySelector('[aria-current]');
      return t ? (cur ? `${t} (current)` : t) : '';
    }).filter(Boolean).join(' > ').slice(0, 300);
  }

  // Only captchas that need a person. The invisible reCAPTCHA badge (bottom-right corner,
  // size=invisible) and reCAPTCHA v3 run by themselves, so they are ignored.
  function detectCaptcha() {
    for (const f of deepAll('iframe')) {
      const src = f.src || '';
      if (!visible(f)) continue;
      const r = f.getBoundingClientRect();
      if (/recaptcha\/(api2|enterprise)\/bframe/.test(src) && r.height > 150 && r.width > 150) return 'reCAPTCHA image challenge';
      if (/recaptcha\/(api2|enterprise)\/anchor/.test(src) && !/[?&]size=invisible/.test(src) && r.height > 40 && !f.closest('.grecaptcha-badge')) return 'reCAPTCHA checkbox';
      if (/hcaptcha\.com/.test(src) && !/[?&]size=invisible/.test(src) && r.height > 40) return 'hCaptcha';
      if (/challenges\.cloudflare\.com/.test(src) && r.height > 40) return 'Cloudflare Turnstile';
    }
    if (/verify (that )?you are (a )?human|are you a robot|press (&|and) hold/i.test(textOf(document.body).slice(0, 3000))) return 'human verification page';
    return '';
  }

  // Captcha is solved when the token field is filled (or the widget went away).
  function captchaSolved() {
    const tok = deepAll('textarea[name="g-recaptcha-response"], textarea[name="h-captcha-response"], input[name="cf-turnstile-response"]');
    if (tok.some((t) => t.value) && !/image challenge/.test(detectCaptcha())) return true;
    return !detectCaptcha();
  }

  // Scrolls the captcha into view so it is the first thing you see when the tab opens.
  function showCaptcha() {
    const f = deepAll('iframe').find((x) => visible(x) && /recaptcha\/(api2|enterprise)\/(anchor|bframe)|hcaptcha|challenges\.cloudflare/.test(x.src || '') && !/size=invisible/.test(x.src || ''));
    if (f) f.scrollIntoView({ block: 'center' });
    return !!f;
  }

  // ---------- cookie banners ----------

  function acceptCookies() {
    for (const sel of COOKIE_IDS) {
      const el = deepAll(sel).find(visible);
      if (el) {
        realClick(el);
        return textOf(el) || sel;
      }
    }
    for (const el of deepAll('button, [role="button"], a, input[type="button"], input[type="submit"]')) {
      if (!visible(el)) continue;
      const t = clean(el.innerText || el.value);
      if (!COOKIE_BUTTON.test(t)) continue;
      let box = el.parentElement;
      for (let i = 0; i < 7 && box; i++, box = box.parentElement) {
        const bt = textOf(box);
        if (bt.length > 1500) break;
        if (COOKIE_WORDS.test(bt)) {
          realClick(el);
          return t;
        }
      }
    }
    return '';
  }

  // ---------- acting ----------

  function realClick(target) {
    // Workday covers some buttons (Create Account, Sign In) with a transparent "click_filter" layer
    const filter = target.querySelector?.('[data-automation-id="click_filter"]')
      || target.parentElement?.querySelector(':scope > [data-automation-id="click_filter"]');
    const el = filter && visible(filter) ? filter : target;
    el.scrollIntoView?.({ block: 'center', inline: 'center' });
    const opts = { bubbles: true, cancelable: true, composed: true, view: window };
    try {
      el.dispatchEvent(new PointerEvent('pointerdown', opts));
      el.dispatchEvent(new MouseEvent('mousedown', opts));
      el.dispatchEvent(new PointerEvent('pointerup', opts));
      el.dispatchEvent(new MouseEvent('mouseup', opts));
    } catch {}
    el.click();
  }

  function setNativeValue(el, value) {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (setter) setter.call(el, value);
    else el.value = value;
  }

  function fire(el, ...types) {
    for (const t of types) el.dispatchEvent(new Event(t, { bubbles: true, composed: true }));
  }

  function toDateInput(v) {
    const s = String(v).trim();
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
    const m = /^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$/.exec(s);
    if (m) return `${m[3]}-${m[1].padStart(2, '0')}-${m[2].padStart(2, '0')}`;
    const d = new Date(s);
    return isNaN(d) ? s : d.toISOString().slice(0, 10);
  }

  function fillText(el, value) {
    el.scrollIntoView?.({ block: 'center' });
    el.focus();
    if (el.isContentEditable) {
      document.execCommand('selectAll', false, null);
      document.execCommand('insertText', false, value);
      fire(el, 'input', 'change');
      el.blur();
      return true;
    }
    const v = el.type === 'date' ? toDateInput(value) : value;
    setNativeValue(el, v);
    el.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: v, inputType: 'insertText' }));
    fire(el, 'change');
    el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'End' }));
    el.blur();
    fire(el, 'blur', 'focusout');
    return el.value === v || el.type === 'password' || el.type === 'date';
  }

  // Best option for a wanted answer: exact, then starts-with, then contains, then word overlap.
  function bestMatch(options, wanted, textFn = (x) => x) {
    const w = clean(wanted).toLowerCase();
    if (!w) return null;
    const norm = (o) => clean(textFn(o)).toLowerCase();
    let hit = options.find((o) => norm(o) === w)
      || options.find((o) => norm(o).startsWith(w) || w.startsWith(norm(o)) && norm(o).length > 1)
      || options.find((o) => norm(o).includes(w) || (norm(o).length > 2 && w.includes(norm(o))));
    if (hit) return hit;
    const words = new Set(w.split(/[^a-z0-9+#]+/).filter((x) => x.length > 1));
    let best = null;
    let score = 0;
    for (const o of options) {
      const ow = norm(o).split(/[^a-z0-9+#]+/).filter((x) => x.length > 1);
      const s = ow.filter((x) => words.has(x)).length / Math.max(ow.length, 1);
      if (s > score) {
        score = s;
        best = o;
      }
    }
    return score >= 0.34 ? best : null;
  }

  function selectNative(el, value) {
    const opts = [...el.options].filter((o) => !o.disabled);
    const hit = bestMatch(opts, value, (o) => o.text) || bestMatch(opts, value, (o) => o.value);
    if (!hit) return false;
    el.focus();
    setNativeValue(el, hit.value);
    hit.selected = true;
    fire(el, 'input', 'change');
    el.blur();
    return true;
  }

  function selectGroup(first, value) {
    const gid = first.dataset.abgroup || first.dataset.abid;
    const inputs = deepAll(`[data-abgroup="${gid}"]`);
    const list = inputs.length ? inputs : [first];
    const wanted = String(value).split('|').map((x) => x.trim()).filter(Boolean);
    let ok = false;
    for (const w of wanted) {
      const hit = bestMatch(list, w, optionLabel);
      if (!hit) continue;
      if (!hit.checked) clickChoice(hit);
      ok = true;
    }
    return ok;
  }

  function clickChoice(input) {
    const label = input.labels?.[0] || input.closest('label');
    if (label && visible(label)) realClick(label);
    else realClick(input);
    if (!input.checked) {
      input.checked = true;
      fire(input, 'input', 'change');
    }
  }

  function setChecked(el, want) {
    if (el.getAttribute('role') === 'checkbox' || el.getAttribute('role') === 'switch') {
      if ((el.getAttribute('aria-checked') === 'true') !== want) realClick(el);
      return true;
    }
    if (el.checked !== want) {
      const label = el.labels?.[0] || el.closest('label');
      if (label && visible(label)) realClick(label);
      else realClick(el);
      if (el.checked !== want) {
        el.checked = want;
        fire(el, 'input', 'change');
      }
    }
    return el.checked === want;
  }

  const OPTION_SEL = '[role="option"], [data-automation-id="promptOption"], [data-automation-id="promptLeafNode"], [class*="option" i]:not(select):not(option), [role="listbox"] li';
  const NOT_AN_OPTION = /^(no (options|results|items|matches)|loading|searching|type to search|start typing|please enter|enter \d+ or more)/i;
  const isOtherOption = (t) => /^(other|others|other \(please specify\)|other:|select|select one|choose one|please select|-{2,}.*)$/i.test(clean(t));

  function visibleOptions(el, input) {
    const listId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || input?.getAttribute?.('aria-controls');
    const scope = listId ? document.getElementById(listId) : null;
    return (scope ? [...scope.querySelectorAll('[role="option"], li')] : deepAll(OPTION_SEL))
      .filter((o) => visible(o) && textOf(o) && textOf(o).length < 200 && !NOT_AN_OPTION.test(textOf(o)));
  }

  function closeDropdown(el, input) {
    for (const t of [input, el, document.activeElement, document.body]) {
      t?.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Escape', code: 'Escape', keyCode: 27 }));
    }
  }

  // Opens every custom dropdown on the page once, reads its choices, closes it again and
  // remembers them (data-abopts), so the snapshot shows the AI what the form offers.
  async function probeOptions() {
    let found = 0;
    for (const el of deepAll('input, [role="combobox"]:not(input), button[aria-haspopup="listbox"]')) {
      if (el.dataset.abopts || el.disabled || !visible(el) || !isCombobox(el)) continue;
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (el.tagName === 'INPUT' && (SKIP_INPUT.has(type) || type === 'radio' || type === 'checkbox')) continue;
      const input = el.tagName === 'INPUT' ? el : el.querySelector('input');
      const before = new Set(deepAll(OPTION_SEL).filter((o) => visible(o)));
      realClick(el);
      let opts = [];
      for (let i = 0; i < 6 && !opts.length; i++) {
        await sleep(150 + i * 60);
        opts = visibleOptions(el, input).filter((o) => !before.has(o));
      }
      const texts = [...new Set(opts.map((o) => clean(textOf(o))))].slice(0, 80);
      el.dataset.abopts = JSON.stringify(texts); // [] = typeahead: suggestions only appear after typing
      if (texts.length) found++;
      closeDropdown(el, input);
      await sleep(120);
      if (visibleOptions(el, input).some((o) => !before.has(o))) realClick(el); // toggle-style dropdowns close on a second click
      input?.blur?.();
      await sleep(80);
    }
    return found;
  }

  let chooseNote = '';

  // Custom dropdowns and typeaheads: open, type, then click the matching option.
  // When the dropdown's options are known, only a real match is picked (never a guess or "Other").
  async function choose(el, value) {
    chooseNote = '';
    const known = el.dataset.abopts ? JSON.parse(el.dataset.abopts) : [];
    const picked = await chooseOption(el, value, known.length > 0);
    if (!picked && known.length) chooseNote = `no option matches "${value}"; the options are: ${known.slice(0, 40).join(' | ')}`;
    return picked;
  }

  async function chooseOption(el, value, strict) {
    const input = el.tagName === 'INPUT' ? el : el.querySelector('input') || el;
    realClick(el);
    await sleep(250);
    if (input.tagName === 'INPUT') {
      input.focus();
      setNativeValue(input, value);
      input.dispatchEvent(new InputEvent('input', { bubbles: true, composed: true, data: value, inputType: 'insertText' }));
      fire(input, 'change');
    }
    let entered = false;
    for (let i = 0; i < 14; i++) {
      await sleep(250 + i * 50);
      const opts = visibleOptions(el, input);
      if (!opts.length) {
        // search boxes (Workday prompts) only list matches after Enter
        if (!entered && i >= 3 && input.tagName === 'INPUT') {
          entered = true;
          for (const type of ['keydown', 'keypress', 'keyup']) input.dispatchEvent(new KeyboardEvent(type, { bubbles: true, key: 'Enter', code: 'Enter', keyCode: 13 }));
        }
        continue;
      }
      // typeaheads with unknown options: after a while take the first real suggestion (never "Other")
      const hit = bestMatch(opts, value, textOf) || (!strict && i > 6 ? opts.find((o) => !isOtherOption(textOf(o))) : null);
      if (hit) {
        realClick(hit);
        await sleep(200);
        return textOf(hit);
      }
    }
    if (strict) {
      closeDropdown(el, input);
      return '';
    }
    // last resort: keyboard pick of the first suggestion
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowDown', code: 'ArrowDown', keyCode: 40 }));
    input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter', keyCode: 13 }));
    await sleep(200);
    return '';
  }

  function b64ToFile(b64, name) {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new File([bytes], name, { type: 'application/pdf', lastModified: Date.now() });
  }

  function findFileInput(el) {
    if (el.tagName === 'INPUT' && el.type === 'file') return el;
    let box = el;
    for (let i = 0; i < 6 && box; i++, box = box.parentElement) {
      const inp = box.querySelector?.('input[type="file"]');
      if (inp) return inp;
    }
    return null;
  }

  async function upload(el, file) {
    const dt = new DataTransfer();
    dt.items.add(b64ToFile(file.b64, file.name));
    const input = findFileInput(el);
    if (input) {
      input.files = dt.files;
      fire(input, 'input', 'change');
      return input.files.length > 0;
    }
    // drop zones without a file input
    for (const type of ['dragenter', 'dragover', 'drop']) {
      el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    }
    return true;
  }

  function withSecrets(value, secrets) {
    return String(value ?? '').replace(/\{\{(\w+)\}\}/g, (m, k) => (secrets && secrets[k] != null ? secrets[k] : m));
  }

  async function act(actions, files, secrets) {
    const results = [];
    for (const a of actions) {
      const el = byId(a.id);
      if (!el) {
        results.push({ id: a.id, ok: false, note: 'element not found' });
        continue;
      }
      const value = withSecrets(a.value, secrets);
      let ok = false;
      let note = '';
      try {
        switch (a.op) {
          case 'fill':
            ok = fillText(el, value);
            break;
          case 'select':
            if (el.dataset.abbtns) {
              // a Yes/No button row: click the button with that answer
              const opts = [...el.querySelectorAll(':scope > button, :scope > [role="button"], :scope > [role="radio"]')];
              const hit = bestMatch(opts, value, textOf);
              if (hit) { realClick(hit); ok = true; } else note = `no option matches "${a.value}"; the options are: ${opts.map(textOf).join(' | ')}`;
            } else if (el.tagName === 'SELECT') ok = selectNative(el, value);
            else if (el.type === 'radio' || el.type === 'checkbox') ok = selectGroup(el, value);
            else {
              note = await choose(el, value); // custom dropdown
              ok = !!note;
            }
            if (!ok) {
              note = el.tagName === 'SELECT'
                ? `no option matches "${a.value}"; the options are: ${[...el.options].map((o) => clean(o.text)).filter(Boolean).slice(0, 40).join(' | ')}`
                : chooseNote || `no option matches "${a.value}"`;
            }
            break;
          case 'check':
          case 'uncheck':
            ok = setChecked(el, a.op === 'check');
            break;
          case 'choose':
            note = await choose(el, value);
            ok = !!note;
            if (!ok) note = chooseNote || 'no matching suggestion';
            break;
          case 'click':
            realClick(el);
            ok = true;
            break;
          case 'upload_resume':
          case 'upload_cover': {
            const f = a.op === 'upload_resume' ? files?.resume : files?.cover;
            if (!f) note = 'file not available';
            else ok = await upload(el, f);
            break;
          }
          default:
            note = 'unknown op ' + a.op;
        }
      } catch (err) {
        note = err.message;
      }
      results.push({ id: a.id, ok, note });
      await sleep(120 + Math.random() * 180);
    }
    return results;
  }

  async function click(id) {
    const el = byId(id);
    if (!el) return false;
    realClick(el);
    return true;
  }

  // Resolves once the DOM has been quiet for `quiet` ms (or after `max` ms).
  function waitStable(quiet = 700, max = 8000) {
    return new Promise((resolve) => {
      let timer;
      const done = () => {
        obs.disconnect();
        clearTimeout(cap);
        resolve(true);
      };
      const obs = new MutationObserver(() => {
        clearTimeout(timer);
        timer = setTimeout(done, quiet);
      });
      obs.observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
      timer = setTimeout(done, quiet);
      const cap = setTimeout(done, max);
    });
  }

  // Longest readable block of text (used to read the full job description).
  function pageText() {
    const main = document.querySelector('main, [role="main"], article, #content, .content, #app') || document.body;
    return { url: location.href, title: document.title, h1: textOf(document.querySelector('h1')), text: textOf(main).slice(0, 20000) };
  }

  window.__abAgent = { snapshot, probeOptions, act, click, acceptCookies, waitStable, captchaSolved, showCaptcha, pageText };
})();
