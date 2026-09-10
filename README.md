# 💬 chatMCD

> **Ask a question about Marc C. Deller and get a straight answer, drawn from his own writing.**

[![live](https://img.shields.io/badge/live-chatmcd.mdeller.com-00d084?logo=icloud&logoColor=white)](https://chatmcd.mdeller.com) ![python](https://img.shields.io/badge/python-3.12.3-3776AB?logo=python&logoColor=white) ![flask](https://img.shields.io/badge/flask-3.1.3-000000?logo=flask&logoColor=white) ![gunicorn](https://img.shields.io/badge/gunicorn-26.2.0-499848?logo=gunicorn&logoColor=white) ![nginx](https://img.shields.io/badge/nginx-1.24.0-009639?logo=nginx&logoColor=white) ![sqlite](https://img.shields.io/badge/sqlite-3.45.1-003B57?logo=sqlite&logoColor=white) ![gradio](https://img.shields.io/badge/gradio-5.49.1-F97316?logo=gradio&logoColor=white) ![model](https://img.shields.io/badge/model-Qwen3--8B-467FF7) ![hosting](https://img.shields.io/badge/inference-ZeroGPU-FFD21E?logo=huggingface&logoColor=black) ![embeddings](https://img.shields.io/badge/embeddings-all--MiniLM--L6--v2-9b51e0) ![pytest](https://img.shields.io/badge/pytest-23%20passing-0A9EDC?logo=pytest&logoColor=white) ![browser](https://img.shields.io/badge/browser%20checks-38%20passing-00897B) ![wordpress](https://img.shields.io/badge/plugin%20tests-48%20passing-21759B?logo=wordpress&logoColor=white) ![licence](https://img.shields.io/badge/licence-MIT-lightgrey) ![author](https://img.shields.io/badge/author-Marc%20C.%20Deller%2C%20D.Phil.-1C244B)

<table>
<tr>
<td>🌐 <b>App</b></td><td><a href="https://chatmcd.mdeller.com" target="_blank" rel="noopener noreferrer">chatmcd.mdeller.com</a></td>
<td>✉️ <b>Contact</b></td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙 <b>GitHub</b></td><td><a href="https://github.com/bellcheddar/chatMCD" target="_blank" rel="noopener noreferrer">bellcheddar/chatMCD</a></td>
</tr>
</table>

---

![The chatMCD web app answering a question about leadership, with the answer rendered as a numbered list](docs/screenshots/app-light.png)

chatMCD is a chatbot that answers questions about Marc C. Deller, D.Phil., a structural biologist and drug discovery scientist. It talks *about* him in the third person, it answers from his own papers, patents, thesis and notes, and when it does not know something it says so instead of guessing.

**Why it matters:** most personal chatbots are a language model with a biography pasted into the prompt, so they invent plausible details the moment a question goes past what was pasted in. chatMCD works the other way round: before it answers anything it looks up the passages that are actually relevant, and it answers from those. That one decision is what makes it safe to point a recruiter, a collaborator or a journalist at. It is useful for: anyone who wants a straight answer about someone's work without reading a CV, and for anyone who wants to build the same thing for themselves, since the whole serving stack is here.

## ✨ How it works

Three pieces, and the middle one is the important one.

1. **You ask a question** in the web app, the embeddable widget, or through the API.
2. **It finds the relevant writing.** Every question is compared against a library of short question-and-answer pairs plus longer passages from Marc's papers and blog. The best matches are pulled out.
3. **A language model writes the answer** using only those passages, and streams it back a word at a time.

Step 2 is what stops it making things up. The model is not asked "what do you know about Marc Deller"; it is handed the relevant paragraphs and asked to answer from them. If nothing relevant comes back, the honest answer is "I do not know", and that is what it gives.

**Why matching questions to questions works so well.** The library is built mostly from short question-and-answer pairs rather than long prose. A question like "what is Elora Therapeutics?" looks, mathematically, far more like another short question than it does like a page of a scientific paper, so the match is much sharper. Longer passages are kept alongside them, because they are the only way to reach the long tail of blog posts that the short pairs do not cover.

**No fine-tuning.** The language model is used exactly as published. Nothing is trained, and there is no custom model to keep in step with the writing: when Marc writes something new, the library is rebuilt and the answers change. This was measured rather than assumed, and it also scored better.

## 📊 How well it works

Measured on a fixed set of 50 questions in six categories, run end to end through the live site:

| Category | What it tests | Score |
|---|---|---:|
| Facts | Roles, employers, publication and structure counts, qualifications | 16/17 |
| Personality | Style, philosophy, the stories behind project names | 8/8 |
| Web | Blog posts, site pages, the other apps | 8/8 |
| Honesty | Things the writing genuinely does not cover: it must decline | 4/4 |
| Manners | Off-topic requests: it must redirect, not comply | 4/5 |
| Depth | Detailed scientific explanations | 5/8 |
| **Overall** | | **45/50 (90%)** |

A typical answer starts arriving in about **5 to 7 seconds**.

Under load (each conversation a different question, so nothing is cached anywhere):

| Simultaneous conversations | Time to first word (95th percentile) | Failures |
|---:|---:|---:|
| 3 | 1.8 s | 0 |
| 8 | 5.9 s | 0 |
| 12 | 6.4 s | 0 |
| 20 | 27.1 s | 0 |

Nothing ever fails: it queues. The queue is the shared GPU, not the web server, so twelve people at once is comfortable and twenty simply wait longer.

## 🧱 How it is put together

| Piece | What it does |
|---|---|
| **Hugging Face Space** | Holds the model and the searchable library, and does the actual answering on a GPU that is attached only for the seconds it is needed |
| **Flask app** | The website at `chatmcd.mdeller.com`. It is the only thing that holds the Hugging Face token, streams answers to the browser, and logs which questions get asked |
| **WordPress plugin** | Drops the chat box into a WordPress page as a shortcode or a block |

The browser never talks to the model directly, and never sees a token.

## 🚀 Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r chatmcd/requirements.txt
cp .env.example .env          # then fill in HF_TOKEN
```

The quickest way to see the front end, with no model and no GPU time spent:

```bash
MOCK_SPACE=1 PORT=8010 .venv/bin/python3 -m chatmcd.app
```

`MOCK_SPACE=1` serves canned answers, so the whole interface can be developed and screenshotted offline.

To rebuild the searchable library after the source writing changes:

```bash
python3 scripts/build_rag_index.py
```

## 🛠️ Configuration

Everything is set in `.env`. The ones that matter:

| Setting | What it does |
|---|---|
| `HF_TOKEN` | Hugging Face token. **Required**, even for a public Space: the anonymous GPU allowance is small, counted per server, and runs out quickly. A read-only token is enough |
| `HF_SPACE_ID` | Which Space to call |
| `KEEPWARM_ENABLED` | Pings the Space periodically so nobody lands on a cold start |
| `RATE_LIMIT` | Requests allowed per visitor, for example `"20 per minute"` |
| `LOG_QUESTIONS` | Whether to record which questions get asked. Email addresses and phone numbers are stripped before anything is written |
| `MOCK_SPACE` | `1` serves canned answers instead of calling the model |

## 🌐 The API

| Route | What it is for |
|---|---|
| `GET /` | The full chat app |
| `GET /embed` | The compact widget, for putting in a page on another site |
| `POST /api/chat` | Send `{message, history}`, get a stream of words back |
| `GET /api/presets` | The suggested-question chips, so no front end hardcodes them |
| `GET /api/health` | Is it up, and is the model warm |
| `POST /api/feedback` | Thumbs up or down on an answer |
| `GET /llms.txt` | A plain description of the page, for crawlers |

Also in place: per-visitor rate limiting, a content security policy, and framing restricted to Marc's own domains so the widget cannot be embedded elsewhere.

## 🔌 WordPress

```
[chatmcd]
[chatmcd mode="bubble" theme="dark" label="Ask about Marc"]
```

A full plugin: shortcode, block, settings screen, inline and floating-bubble modes. The bubble does not load the widget until someone opens it, so a visitor who ignores it downloads nothing.

```bash
bash scripts/build_plugin_zip.sh      # lints, tests, then zips
```

Full instructions in [`docs/EMBED.md`](docs/EMBED.md).

## 🧪 Tests

```bash
.venv/bin/python3 -m pytest chatmcd/ eval/     # 18 tests
.venv-gradio/bin/python3 -m pytest space/      # 5 tests, needs gradio
php wordpress/tests/test_plugin.php            # 48 tests
python3 scripts/browser_check.py               # 38 checks in a real browser
```

The browser checks drive a real Chrome over the DevTools protocol rather than taking a screenshot and hoping: they cover streaming, the markdown rendering, the copy and voting buttons, the light and dark themes, the widget, and a clean console.

```bash
python3 scripts/load_test.py --n 12            # concurrent conversations
```

## 🧱 Layout

```
chatMCD/
├── chatmcd/            the Flask app, its templates, CSS and JavaScript
├── space/              the Hugging Face Space that does the answering
├── wordpress/          the plugin and its tests
├── eval/               the scoring harness
├── scripts/            index build, browser checks, load test, question digest
├── deploy/             gunicorn, systemd, nginx, deploy script
├── training/           the scripts that build the searchable library
└── docs/               embedding guide, model notes, screenshots
```

## ✅ To Do

Roadmap for chatMCD, newest first. Suggestions welcome.

- [x] **Live at [chatmcd.mdeller.com](https://chatmcd.mdeller.com).** Flask behind nginx with TLS, answers streaming word by word, conversation history carried between turns, light and dark themes with light as the default.
- [x] **Answers from the writing, not from the model's memory.** Relevant passages are retrieved for every question and the model answers from those. Measured at 45/50 on a fixed 50-question set, including 4/4 on questions it is supposed to refuse.
- [x] **Load tested.** Twelve simultaneous conversations stay inside a ten second budget with no failures, and it queues rather than erroring above that.
- [x] **Embeddable anywhere on Marc's sites.** A compact widget plus a WordPress plugin with a shortcode, a block and a settings screen.
- [x] **Tested where it actually runs.** The published API contract, the failure handling, the scoring, the plugin, and 38 checks driving a real browser.
- [ ] **Stop it writing poems and code.** The one weak spot. Asked for a sorting function it writes one, despite being told not to. The redirect examples it needs are missing from the library rather than being ignored, so the fix is more example refusals, not more instructions.
- [ ] **Say so when nothing relevant is found.** Right now an empty search still gets an answer attempt. It should decline instead: this is the main remaining lever on honesty.
- [ ] **Feed the questions back in.** `scripts/digest.py` groups what visitors actually asked by how often they asked it. The long tail is a list of things the writing does not yet cover.

## 📄 Licence

MIT, for the code. Marc's writing (his papers, patents, thesis, notes and the question-and-answer pairs built from them) is **not** included in this repository and stays under its original copyright. See [`LICENSE`](LICENSE), section SCOPE.

---

## 👤 Author

**Marc C. Deller, D.Phil.**  
Structural biologist & drug discovery scientist  

<table>
<tr>
<td>🌐</td><td><a href="https://marcdeller.com" target="_blank" rel="noopener noreferrer">marcdeller.com</a></td>
<td>✉️</td><td><a href="mailto:marc@marcdeller.com">marc@marcdeller.com</a></td>
<td>🐙</td><td><a href="https://github.com/bellcheddar/chatMCD" target="_blank" rel="noopener noreferrer">github.com/bellcheddar/chatMCD</a></td>
</tr>
</table>
