/* =============================================================================
   chatMCD front end — shared by / and /embed.

   No framework and no CDN: the page's CSP is `script-src 'self'`, so everything
   here is vanilla and the markdown renderer is hand-rolled rather than pulled
   from a library.

   Streaming uses fetch + a manual SSE parser rather than EventSource, because
   the request is a POST carrying the conversation history.
   ========================================================================== */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const body = document.body;
  const MODE = body.dataset.mode || 'app';
  const IS_EMBED = MODE === 'embed';

  const transcript = $('#transcript');
  const composer = $('#composer');
  const input = $('#q');
  const sendBtn = $('#send');
  const chips = $('#chips');
  const hint = $('#hint');

  /** Conversation so far, as [{role, content}]. Sent with every request. */
  const history = [];
  let controller = null;   // AbortController for the in-flight generation

  /* ------------------------------------------------------------------ theme */

  const THEME_KEY = 'chatmcd-theme';

  function setTheme(name, persist) {
    const theme = name === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll('[data-theme-set]').forEach((b) => {
      b.setAttribute('aria-pressed', String(b.dataset.themeSet === theme));
    });
    if (persist) {
      try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* private mode */ }
    }
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-theme-set]');
    if (btn) setTheme(btn.dataset.themeSet, true);
  });

  // The inline head script already applied the stored theme; sync the buttons.
  setTheme(document.documentElement.dataset.theme, false);

  /* --------------------------------------------------------------- markdown */

  const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ESC[c]);

  /** Inline spans, applied to already-escaped text. */
  function inline(s) {
    return s
      .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      // Bare URLs and emails the model writes out in prose.
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g,
        '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
      .replace(/(^|[\s(])([\w.+-]+@[\w-]+\.[\w.]{2,})/g, '$1<a href="mailto:$2">$2</a>');
  }

  /**
   * Render a small, safe subset of markdown: fenced code, headings, bullet and
   * numbered lists, paragraphs. Everything is escaped before any tag is added,
   * so model output can never inject markup.
   */
  /** One table row: split on | and drop the empty cells the outer pipes make. */
  function cells(line) {
    return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());
  }
  const SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
  const align = (c) => (c.startsWith(':') && c.endsWith(':') ? ' style="text-align:center"'
    : c.endsWith(':') ? ' style="text-align:right"' : '');

  function md(src) {
    const lines = esc(src).split('\n');
    const out = [];
    let fence = null;     // accumulating code block

    // A STACK, not a single list. Asking the model for richer answers made it
    // write nested lists -- bullets indented under a numbered step -- and a flat
    // parser treats the first sub-bullet as the end of the parent list. The next
    // numbered item then opens a fresh <ol>, which every browser numbers from 1
    // again. Same "1. 1. 1." symptom as the loose-list bug, different cause.
    const stack = [];     // [{ tag, indent, insideLi }]

    /** Open a list, nesting it INSIDE the previous <li> rather than beside it,
     *  so the markup stays valid and the sub-list indents under its parent. */
    const openList = (tag, indent) => {
      let insideLi = false;
      if (stack.length && out.length && out[out.length - 1].endsWith('</li>')) {
        out.push(out.pop().replace(/<\/li>$/, ''));
        insideLi = true;
      }
      out.push(`<${tag}>`);
      stack.push({ tag, indent, insideLi });
    };
    const popList = () => {
      const f = stack.pop();
      out.push(`</${f.tag}>` + (f.insideLi ? '</li>' : ''));
    };
    const closeList = () => { while (stack.length) popList(); };
    /** Close any level indented deeper than this line. */
    const closeDeeper = (indent) => {
      while (stack.length && stack[stack.length - 1].indent > indent) popList();
    };

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];

      if (fence !== null) {
        if (/^```/.test(line)) { out.push(`<pre><code>${fence.join('\n')}</code></pre>`); fence = null; }
        else fence.push(line);
        continue;
      }
      if (/^```/.test(line)) { closeList(); fence = []; continue; }

      // A table: a pipe row whose NEXT line is the |---|---| separator. Needs
      // one line of lookahead, which is why this loop is indexed. While the
      // answer is still streaming the separator has not arrived yet, so the
      // header renders as an ordinary paragraph for a moment and then becomes a
      // table: no half-drawn markup at any point.
      if (/\|/.test(line) && line.trim().startsWith('|') && SEP.test(lines[i + 1] || '')) {
        closeList();
        const head = cells(line);
        const al = cells(lines[i + 1]).map(align);
        const body = [];
        let j = i + 2;
        for (; j < lines.length; j += 1) {
          const row = lines[j];
          if (!row.trim() || !row.trim().startsWith('|')) break;
          body.push(cells(row));
        }
        out.push('<div class="md-table"><table><thead><tr>'
          + head.map((c, k) => `<th${al[k] || ''}>${inline(c)}</th>`).join('')
          + '</tr></thead><tbody>'
          + body.map((r) => '<tr>'
            + r.map((c, k) => `<td${al[k] || ''}>${inline(c)}</td>`).join('')
            + '</tr>').join('')
          + '</tbody></table></div>');
        i = j - 1;
        continue;
      }

      // A bare list marker with nothing after it. The model emits one now and
      // then when it stops mid-list, and it rendered as a stray "-" hanging
      // under the answer. Nothing to show, so show nothing.
      if (/^\s*([-*+]|\d+[.)])\s*$/.test(line)) continue;

      // A horizontal rule, but only outside a table (|---| is handled above).
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { closeList(); out.push('<hr>'); continue; }

      const h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        closeList();
        // A real heading, not a bold paragraph: it carries the accent colour and
        // gives a long answer a scannable spine.
        const lvl = Math.min(h[1].length + 2, 5);
        out.push(`<h${lvl} class="md-h">${inline(h[2])}</h${lvl}>`);
        continue;
      }

      // A callout. The model uses it for the one thing worth pulling out of a
      // long answer, and it renders with an accent bar rather than as a quote.
      // Matched as &gt; because esc() runs over the whole source before this
      // parser sees a line, so a markdown "> " has already become "&gt; ".
      const BQ = /^\s*&gt;\s?(.*)$/;
      const bq = line.match(BQ);
      if (bq) {
        closeList();
        const buf = [bq[1]];
        let j = i + 1;
        for (; j < lines.length; j += 1) {
          const m = lines[j].match(BQ);
          if (!m) break;
          buf.push(m[1]);
        }
        out.push(`<div class="md-note">${inline(buf.join(' ').trim())}</div>`);
        i = j - 1;
        continue;
      }

      const ul = line.match(/^(\s*)[-*+]\s+(.*)$/);
      const ol = ul ? null : line.match(/^(\s*)\d+[.)]\s+(.*)$/);
      const item = ul || ol;
      if (item) {
        const tag = ul ? 'ul' : 'ol';
        const indent = item[1].length;
        closeDeeper(indent);
        const top = stack[stack.length - 1];
        if (!top || indent > top.indent) {
          openList(tag, indent);
        } else if (top.tag !== tag) {
          // Same depth, different kind: a bulleted list following a numbered one.
          popList();
          openList(tag, indent);
        }
        out.push(`<li>${inline(item[2])}</li>`);
        continue;
      }

      // A wrapped continuation line belongs to the item above, not to a new
      // block. Markdown indents it, and closing the list here started a fresh
      // <ol> on the next item -- which is why every step of a numbered answer
      // rendered as "1.".
      if (stack.length && /^\s{2,}\S/.test(line) && out[out.length - 1].endsWith('</li>')) {
        out.push(out.pop().replace(/<\/li>$/, ' ' + inline(line.trim()) + '</li>'));
        continue;
      }

      // A blank line does NOT end a list. The model writes loose lists -- a
      // blank line between every item -- which is ordinary markdown, and
      // closing on it wrapped each item in its own <ol>. Every browser numbers
      // a fresh <ol> from 1, so a six-step answer came out as "1." six times.
      // Every other branch below already closes the list when it genuinely
      // ends, so nothing needs to close it here.
      if (!line.trim()) continue;

      closeList();
      out.push(`<p>${inline(line)}</p>`);
    }
    if (fence !== null) out.push(`<pre><code>${fence.join('\n')}</code></pre>`);
    closeList();
    return out.join('');
  }

  /* Exposed so scripts/browser_check.py can put a FIXED string through the real
     renderer. The check used to assert that <strong> and <ul> appeared in a live
     answer, which only held when the model happened to emit bold and a list --
     it reported two failures against a renderer that was working perfectly. A
     pure string-to-string function; exposing it grants a caller nothing. */
  window.chatmcdRenderMarkdown = md;

  /* -------------------------------------------------------------- transcript */

  const atBottom = () =>
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;

  function scroll(force) {
    if (force || atBottom()) {
      transcript.scrollTo({
        top: transcript.scrollHeight,
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'auto' : 'smooth',
      });
    }
  }

  function addMessage(role, text) {
    if (hint && hint.parentNode) hint.remove();
    const el = document.createElement('div');
    el.className = `msg ${role === 'user' ? 'user' : 'bot'}`;
    const who = document.createElement('span');
    who.className = 'who';
    who.textContent = role === 'user' ? 'Visitor' : 'chatMCD';
    const bodyEl = document.createElement('div');
    bodyEl.className = 'md';
    bodyEl.innerHTML = md(text);
    el.append(who, bodyEl);
    transcript.appendChild(el);
    scroll(true);
    return { el, bodyEl };
  }

  function addTyping() {
    const el = document.createElement('div');
    el.className = 'msg bot';
    el.innerHTML = '<span class="who">chatMCD</span>' +
      '<span class="typing" role="status" aria-label="chatMCD is typing">' +
      '<i></i><i></i><i></i></span>';
    transcript.appendChild(el);
    scroll(true);
    return el;
  }

  function addStatus(detail) {
    const el = document.createElement('div');
    el.className = 'status';
    el.textContent = detail;
    transcript.appendChild(el);
    scroll(true);
    return el;
  }

  const ICON_UP = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 10v11H4V10zM7 10l5-8a2.5 2.5 0 0 1 2.4 3.2L13.5 9H19a2 2 0 0 1 2 2.4l-1.6 7A2 2 0 0 1 17.4 20H7"/></svg>';
  const ICON_DOWN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 14V3h3v11zM17 14l-5 8a2.5 2.5 0 0 1-2.4-3.2l.9-3.8H5a2 2 0 0 1-2-2.4l1.6-7A2 2 0 0 1 6.6 4H17"/></svg>';
  const ICON_COPY = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';

  /** Copy + thumbs, appended to a finished answer. */
  function addActions(el, question, answer) {
    const bar = document.createElement('div');
    bar.className = 'actions';

    const copy = document.createElement('button');
    copy.type = 'button';
    copy.innerHTML = `${ICON_COPY}<span>Copy</span>`;
    copy.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(answer);
        copy.querySelector('span').textContent = 'Copied';
        setTimeout(() => { copy.querySelector('span').textContent = 'Copy'; }, 1600);
      } catch (e) { copy.querySelector('span').textContent = 'Press ⌘C'; }
    });

    const vote = (v, icon, label) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('aria-pressed', 'false');
      b.setAttribute('aria-label', label);
      b.innerHTML = icon;
      b.addEventListener('click', () => {
        if (b.getAttribute('aria-pressed') === 'true') return;
        bar.querySelectorAll('[aria-pressed]').forEach((x) =>
          x.setAttribute('aria-pressed', 'false'));
        b.setAttribute('aria-pressed', 'true');
        fetch('/api/feedback', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vote: v, question, source: IS_EMBED ? 'iframe' : 'app' }),
        }).catch(() => {});
      });
      return b;
    };

    bar.append(copy, vote(1, ICON_UP, 'Helpful'), vote(-1, ICON_DOWN, 'Not helpful'));

    // Measured BEFORE the bar is appended, because appending it is what changes
    // scrollHeight. The bar arrives after the last token, so the scrolling done
    // during streaming has already finished -- and without this the copy and
    // thumbs sit just below the fold on every answer that fills the panel:
    // present, focusable, and invisible.
    const stick = atBottom();
    el.appendChild(bar);
    if (stick) scroll(true);
  }

  /* ------------------------------------------------------------------ send */

  function setBusy(busy) {
    sendBtn.classList.toggle('stop', busy);
    sendBtn.setAttribute('aria-label', busy ? 'Stop generating' : 'Send');
    sendBtn.innerHTML = busy
      ? '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.6"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 19V5M5 12l7-7 7 7"/></svg>';
    chips.querySelectorAll('.chip').forEach((c) => { c.disabled = busy; });
  }

  async function ask(question) {
    if (!question.trim() || controller) return;

    addMessage('user', question);
    history.push({ role: 'user', content: question });

    const typing = addTyping();
    let statusEl = null;
    let target = null;
    let answer = '';

    controller = new AbortController();
    setBusy(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        signal: controller.signal,
        body: JSON.stringify({
          message: question,
          history: history.slice(0, -1),
          source: IS_EMBED ? 'iframe' : 'app',
        }),
      });

      if (!res.ok || !res.body) {
        throw new Error(res.status === 429
          ? 'That is a lot of questions at once. Give it a minute and try again.'
          : `The server returned ${res.status}.`);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let event = 'message';
          let data = '';
          for (const line of frame.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim();
            else if (line.startsWith('data:')) data += line.slice(5).trim();
          }
          if (!data) continue;
          let payload;
          try { payload = JSON.parse(data); } catch (e) { continue; }

          if (event === 'status') {
            if (!statusEl) statusEl = addStatus(payload.detail || 'Warming up…');
            else statusEl.textContent = payload.detail || 'Warming up…';
          } else if (event === 'token') {
            if (statusEl) { statusEl.remove(); statusEl = null; }
            if (!target) { typing.remove(); target = addMessage('assistant', ''); }
            answer += payload.t;
            target.bodyEl.innerHTML = md(answer);
            scroll(false);
          } else if (event === 'error') {
            throw new Error(payload.detail || 'The model could not be reached.');
          }
        }
      }

      if (statusEl) statusEl.remove();
      if (!target) {
        typing.remove();
        target = addMessage('assistant',
          'I did not get an answer back that time. Try again, or email ' +
          'marc@marcdeller.com if it keeps happening.');
      } else {
        history.push({ role: 'assistant', content: answer });
        addActions(target.el, question, answer);
      }
    } catch (err) {
      if (statusEl) statusEl.remove();
      if (typing.parentNode) typing.remove();
      if (err.name === 'AbortError') {
        // Stopped on purpose: keep whatever streamed in as a real turn.
        if (target && answer) {
          history.push({ role: 'assistant', content: answer });
          addActions(target.el, question, answer);
        }
      } else if (!target) {
        const el = addMessage('assistant', '');
        el.el.classList.add('error');
        el.bodyEl.textContent = err.message +
          ' You can always reach the real Marc at marc@marcdeller.com.';
      }
    } finally {
      controller = null;
      setBusy(false);
      input.focus();
    }
  }

  /* -------------------------------------------------------------- listeners */

  composer.addEventListener('submit', (e) => {
    e.preventDefault();
    if (controller) { controller.abort(); return; }
    const q = input.value;
    input.value = '';
    autosize();
    ask(q);
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      composer.requestSubmit();
    }
  });

  function autosize() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
  }
  input.addEventListener('input', autosize);

  chips.addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (chip && !chip.disabled) ask(chip.dataset.prompt);
  });

  /* ------------------------------------------------------- widget extras */

  const menu = $('#menu');
  if (menu) {
    const btn = menu.querySelector('button');
    const list = menu.querySelector('ul');
    btn.addEventListener('click', () => {
      const open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', String(!open));
      list.hidden = open;
    });
    document.addEventListener('click', (e) => {
      if (!menu.contains(e.target)) {
        btn.setAttribute('aria-expanded', 'false');
        list.hidden = true;
      }
    });
  }

  // Height reporting, so an embedding page can size the iframe to the content.
  if (IS_EMBED && window.parent !== window) {
    const post = () => {
      window.parent.postMessage({
        type: 'chatmcd:height',
        height: Math.ceil(document.documentElement.scrollHeight),
      }, '*');
    };
    new ResizeObserver(post).observe(document.documentElement);
    window.addEventListener('load', post);
  }

  /* ------------------------------------------------------------- export */

  const exportBtn = $('#export');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => {
      const lines = history.map((m) =>
        `${m.role === 'user' ? 'You' : 'chatMCD'}: ${m.content}`).join('\n\n');
      const bodyText = encodeURIComponent(
        `My conversation with chatMCD (${location.origin})\n\n${lines}\n`);
      location.href =
        `mailto:?subject=${encodeURIComponent('chatMCD — about Marc C. Deller')}` +
        `&body=${bodyText}`;
    });
  }

  input.focus({ preventScroll: true });
})();
