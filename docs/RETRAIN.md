# Improving chatMCD

The loop that makes chatMCD better: visitors ask things it answers badly, those
become new Q&A pairs, the index picks them up, and the evaluation proves nothing
else broke.

**There is no training step.** Retrieval over the Q&A pairs is the mechanism, so
a new fact is live as soon as the index is rebuilt: minutes, not hours. That is
the point of the architecture, and it is what makes this loop worth running
weekly rather than quarterly.

Everything below runs on the Mac.

---

## The daily digest starts the loop for you

`scripts/daily_digest.py` runs from cron on the droplet at 07:15 UTC and emails
the day's questions, the answers they got, roughly where each visitor was, on
what device, and how long each took. Anything it marks **declined** is a question
chatMCD could not answer, which is exactly the list this loop exists to shorten.

```bash
python3 scripts/daily_digest.py --dry-run      # write the HTML, send nothing
python3 scripts/daily_digest.py --hours 168    # a week, on demand
```

It posts to a transactional email API rather than using SMTP, because
DigitalOcean blocks every outbound SMTP port on this droplet: 25, 465, 587 and
2525 all refuse a TCP connection, while port 443 is open. Set `RESEND_API_KEY`
and `DIGEST_TO` in `/opt/chatmcd/.env`. Visitor addresses are resolved to a
country and city at digest time only, in one batch call, for the few addresses
that actually asked something.

---

## The loop, in full

```bash
# 1. what did people actually ask?
scp root@droplet:/opt/chatmcd/var/chatmcd.sqlite var/chatmcd.sqlite
python3 scripts/digest.py --db var/chatmcd.sqlite --days 7 > docs/digests/$(date +%F).md

# 2. write new Q&A pairs into training/qa/<area>/*.qa.md   (see below)

# 3. rebuild the datasets and the index
python3 training/build_jsonl.py
python3 scripts/build_rag_index.py

# 4. check the instruments before trusting any number
python3 eval/audit_questions.py

# 5. measure
python3 eval/run_eval.py --backend mlx --model Qwen/Qwen3-8B --rag --label $(date +%F)

# 6. ship the index: the Space reads space/index/
```

Steps 3 to 6 take about ten minutes. Step 2 is the only part that needs thought.

---

## 1. Pull the week's questions

The digest ranks **distinct questions by how often they were asked**, not
individual rows. Work down that list rather than through the log: one good Q&A
pair usually fixes a whole cluster, and reviewing instances one at a time is how
a 250-decision job becomes a 1,484-row one.

Three things to act on, in order:

1. **Thumbs down.** Something was wrong. Find the right answer in the corpus and
   write the pair.
2. **Repeated questions that got a decline.** The corpus does not cover something
   people keep asking about. Add the source document first, then the pairs.
3. **The long tail.** Skim it. Most of it is fine to leave.

---

## 2. Write the new pairs

New pairs go into the matching `training/qa/<area>/*.qa.md`, or a new file:

```markdown
---
topic: "Short description of what this file covers"
source: "corpus/<area>/<document>.md"
---

Q: The question, phrased the way a visitor asked it.
A: The answer, in the third person, self-contained, grounded in the source.
```

Rules that are not negotiable:

- **Third person, always.** "Marc solved...", never "I solved...".
- **Self-contained.** An answer that says "as mentioned above" teaches the model
  to say that to someone who has not read anything above.
- **Phrase the question the way a visitor would.** This matters more than it used
  to: the question is what gets embedded, and retrieval works by matching a
  visitor's phrasing to yours. A pair written in house jargon will not be found.
- **Never invent a number, a paper or a date.** If the corpus does not say, the
  right answer is a decline pointing at `marc@marcdeller.com`.
- **A decline may name a topic area, never a specific fact.** "I don't know his
  favourite film; I'm better on his crystallography" is fine. "I don't know his
  favourite film, but his favourite band is X" is not: it teaches the shape
  "decline, then state a specific thing", and the specific thing gets invented.
- **No compound data.** No DCV codenames, no SMILES, no compound names. Assume
  this repository is public.

If the answer needs a source that is not in the corpus yet, add the document to
`training/corpus/<area>/` with the frontmatter fields `title, type, year,
authors, venue, doi, marc_role, source_file` first. `fetch_sources.py` refreshes
the web material; the Q&A for it is hand-written and does not refresh, but new
documents become retrievable through the chunk half of the index immediately.

---

## 3. Rebuild

```bash
python3 training/build_jsonl.py
python3 scripts/build_rag_index.py
```

Check the counts against the last run. A large unexplained change means
something was parsed differently, not that a lot was added.

The index is **hybrid, and both halves are load-bearing**:

| | Short-fact recall | Blog long tail |
|---|---:|---:|
| Q&A pairs only, k=8 | 10/10 | **1/23** |
| **8 Q&A + 3 chunks** | **10/10** | **21/23** |

The Q&A pairs answer the short factual questions. The corpus chunks are the only
route to the 215 blog posts, which around 2.4 pairs each cannot cover. Dropping
the chunks scores perfectly on the evaluation and leaves the model unable to
discuss half of what Marc has written: a failure the evaluation does not catch,
because its web questions are all about headline apps that do have pairs.

---

## 4. Audit before you measure

```bash
python3 eval/audit_questions.py
```

Must report **0 blockers**. It checks four things that have each been wrong at
least once:

- every expected string actually exists in `corpus/` or `qa/`, so no question is
  unanswerable;
- every honesty topic is genuinely absent, so an answerable question is not
  sitting in the bucket that measures declining;
- no `forbid` pattern matches the corpus's own wording, so a correct answer is
  not rejected;
- every expected string survives retrieval at the configured top-k.

Run this **before** the evaluation, not after. Everything downstream of a bad
instrument is wasted work.

---

## 5. Measure

```bash
python3 eval/run_eval.py --backend mlx --model Qwen/Qwen3-8B --rag --label $(date +%F)
```

50 questions, six buckets. **The gate is facts at least 90% and honesty at least
3 of 4.** `run_eval.py` exits non-zero until both hold.

Two rules:

- **Never edit `questions.yaml` while a measurement is running.**
  `eval/QUESTIONS_SHA` pins the file; if it has changed, earlier and later
  numbers are not comparable.
- **Compare against the previous week's report**, not a memory of it.
  `eval/reports/*.md` are kept for exactly this.

To check the deployed system rather than the local one:

```bash
python3 eval/run_eval.py --backend api \
  --url https://chatmcd.mdeller.com/api/chat --label live-$(date +%F)
```

That is the only command that proves the whole chain works.

---

## 6. Ship the index

The Space reads `space/index/`. Commit `embeddings.npy` and `chunks.json`, push,
and restart the Space. Then verify the live thing, not the deploy's exit code:

```bash
curl -s https://chatmcd.mdeller.com/api/health
```

---
