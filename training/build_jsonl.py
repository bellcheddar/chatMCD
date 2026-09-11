#!/usr/bin/env python3
"""Build MLX-LM fine-tuning datasets for chatMCD from the Markdown corpus.

Usage:
    python3 build_jsonl.py            # writes mlx/chat, mlx/text and mlx/combined
    python3 build_jsonl.py --seed 7   # different shuffle

Inputs (relative to this script):
    corpus/**/*.md      clean full-text documents (YAML frontmatter + Markdown)
    qa/**/*.qa.md       Q&A files ("Q: ...\nA: ..." pairs, blank-line separated)
    system_prompt.md    the chatMCD system prompt

Outputs:
    mlx/chat/{train,valid,test}.jsonl      {"messages": [system, user, assistant]}   <- Q&A pairs
    mlx/text/{train,valid,test}.jsonl      {"text": "..."}                            <- corpus chunks
    mlx/combined/{train,valid,test}.jsonl  chat format: Q&A pairs + corpus chunks wrapped as
                                           "Share the full text of <title> (part N)" turns

Train with e.g.:
    mlx_lm.lora --model <hf-model> --train --data mlx/combined --iters 1200 \
        --batch-size 2 --num-layers 16 --learning-rate 1e-5 --max-seq-length 4096
"""
import argparse, json, os, random, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))
CHUNK_CHARS = 6000          # ~1500 tokens; keep under --max-seq-length
MIN_CHUNK_CHARS = 800

def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()

def _join_answer(lines):
    """An answer's lines, joined the way they were written.

    This used to be " ".join(line.strip() for non-blank lines), which flattened
    every structured answer onto a single line: tables, bulleted lists, nested
    lists and paragraph breaks all gone before retrieval or the model saw them.
    The model then copied the flattening, and the quick-resume preset rendered as
    "The headline numbers: | | | |---|---| | ...". Newlines, blank lines between
    paragraphs and the indentation of nested list items are now kept, with runs
    of blank lines collapsed to one.
    """
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def split_frontmatter(md):
    m = re.match(r"^---\n(.*?)\n---\n", md, re.S)
    if not m:
        return {}, md
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta, md[m.end():]

def parse_qa(path):
    meta, body = split_frontmatter(read(path))
    # Strip HTML comments before parsing. Any non-"Q: "/"A: " line is otherwise
    # appended to the preceding answer, so a comment written after a pair would
    # silently become training text.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    pairs, q, a = [], None, []
    for line in body.splitlines():
        if line.startswith("Q: "):
            if q and a:
                pairs.append((q, _join_answer(a)))
            q, a = line[3:].strip(), []
        elif line.startswith("A: "):
            a = [line[3:].strip()]
        elif a:
            # Blank lines and leading indentation are part of the answer's
            # formatting; trailing blanks are trimmed by _join_answer.
            a.append(line.rstrip())
    if q and a:
        pairs.append((q, _join_answer(a)))
    bad = [p for p in pairs if len(p[1]) < 10]
    if bad:
        raise SystemExit(f"{path}: {len(bad)} empty answers")
    return meta, pairs

def parse_convo(path):
    """Parse a multi-turn conversation file: blocks of alternating U:/A: lines,
    separated by a line containing only '==='. Each block becomes one multi-turn
    conversation (list of (role, content) tuples in order)."""
    meta, body = split_frontmatter(read(path))
    blocks = re.split(r"\n===\s*\n", body)
    conversations = []
    for block in blocks:
        turns, role, buf = [], None, []
        for line in block.splitlines():
            if line.startswith("U: "):
                if role and buf:
                    turns.append((role, " ".join(buf).strip()))
                role, buf = "user", [line[3:].strip()]
            elif line.startswith("A: "):
                if role and buf:
                    turns.append((role, " ".join(buf).strip()))
                role, buf = "assistant", [line[3:].strip()]
            elif line.strip() and role:
                buf.append(line.strip())
        if role and buf:
            turns.append((role, " ".join(buf).strip()))
        if turns:
            bad = [t for t in turns if len(t[1]) < 2]
            if bad:
                raise SystemExit(f"{path}: empty turn found")
            conversations.append(turns)
    return meta, conversations

def chunk_document(path):
    meta, body = split_frontmatter(read(path))
    title = meta.get("title") or os.path.basename(path)
    header = f"# {title}\n"
    if meta.get("authors"):
        header += f"Authors: {meta['authors']}\n"
    if meta.get("venue"):
        header += f"Source: {meta['venue']}" + (f", {meta['year']}" if meta.get("year") else "") + "\n"
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    body = re.sub(r"^# .*\n", "", body, count=1)  # title already in header
    paras = body.split("\n\n")
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) + 2 > CHUNK_CHARS and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += p + "\n\n"
    if cur.strip():
        if chunks and len(cur) < MIN_CHUNK_CHARS:
            chunks[-1] += "\n\n" + cur.strip()
        else:
            chunks.append(cur.strip())
    n = len(chunks)
    out = []
    for i, c in enumerate(chunks, 1):
        part = f" (part {i} of {n})" if n > 1 else ""
        out.append((title, part, header + "\n" + c))
    return out

def split(items, seed):
    rnd = random.Random(seed)
    items = items[:]
    rnd.shuffle(items)
    n = len(items)
    n_valid = max(1, int(n * 0.05))
    n_test = max(1, int(n * 0.05))
    return {"train": items[: n - n_valid - n_test],
            "valid": items[n - n_valid - n_test: n - n_test],
            "test": items[n - n_test:]}

def write_jsonl(d, splits):
    os.makedirs(d, exist_ok=True)
    for name, rows in splits.items():
        with open(os.path.join(d, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    system = read(os.path.join(HERE, "system_prompt.md")).strip()

    chat_rows, n_files = [], 0
    for p in sorted(glob.glob(os.path.join(HERE, "qa", "**", "*.qa.md"), recursive=True)):
        _, pairs = parse_qa(p)
        n_files += 1
        for q, a in pairs:
            chat_rows.append({"messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": q},
                {"role": "assistant", "content": a}]})
    print(f"Q&A: {len(chat_rows)} pairs from {n_files} files")

    convo_rows, n_convo_files, n_convos = [], 0, 0
    for p in sorted(glob.glob(os.path.join(HERE, "qa", "**", "*.convo.md"), recursive=True)):
        _, conversations = parse_convo(p)
        n_convo_files += 1
        for turns in conversations:
            n_convos += 1
            messages = [{"role": "system", "content": system}]
            for role, content in turns:
                messages.append({"role": role, "content": content})
            convo_rows.append({"messages": messages})
    if n_convo_files:
        print(f"Multi-turn: {n_convos} conversations from {n_convo_files} files")
    chat_rows += convo_rows

    text_rows, wrapped_rows, n_docs = [], [], 0
    for p in sorted(glob.glob(os.path.join(HERE, "corpus", "**", "*.md"), recursive=True)):
        n_docs += 1
        for title, part, text in chunk_document(p):
            text_rows.append({"text": text})
            wrapped_rows.append({"messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Share the full text of \"{title}\"{part}."},
                {"role": "assistant", "content": text}]})
    chars = sum(len(r["text"]) for r in text_rows)
    print(f"Corpus: {n_docs} documents -> {len(text_rows)} chunks, {chars/1e6:.2f} M chars (~{chars/4/1e6:.2f} M tokens)")

    write_jsonl(os.path.join(HERE, "mlx", "chat"), split(chat_rows, args.seed))
    write_jsonl(os.path.join(HERE, "mlx", "text"), split(text_rows, args.seed))
    write_jsonl(os.path.join(HERE, "mlx", "combined"), split(chat_rows + wrapped_rows, args.seed))
    for d in ("chat", "text", "combined"):
        counts = {s: sum(1 for _ in open(os.path.join(HERE, "mlx", d, f"{s}.jsonl"))) for s in ("train", "valid", "test")}
        print(f"mlx/{d}: {counts}")

if __name__ == "__main__":
    main()
