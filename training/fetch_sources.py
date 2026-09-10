#!/usr/bin/env python3
"""Fetch Marc Deller's web sources into corpus/ as Markdown (rerun to refresh).

Sources
  marcdeller.com  WordPress REST API  -> corpus/web/pages/*.md, corpus/web/posts/*.md
  Europe PMC      REST API            -> corpus/reference/publication-abstracts.md
  GitHub          raw README.md       -> corpus/projects/readme-<repo>.md
                  (repo names discovered from github.com/bellcheddar/<repo> links on the site)

Requires: requests (or urllib), markdownify, beautifulsoup4.  python3 fetch_sources.py
"""
import json, os, re, sys, time, html, urllib.request, urllib.parse
from markdownify import markdownify as md
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = "https://marcdeller.com"
GITHUB_USER = "bellcheddar"
EPMC_QUERY = 'AUTH:"Deller MC" OR AUTH:"Deller M"'
UA = {"User-Agent": "chatMCD-fetch/1.0 (marc@marcdeller.com)"}

# Europe PMC returns other "M Deller"s; keep only work in Marc's fields from 1999 on
EXCLUDE_TITLE_WORDS = ["strabism", "amblyop", "calorie", "covid", "olfactory", "vasculog",
                       "oestrogen", "estrogen", "teratocarcinoma", "embryonal", "egf receptor",
                       "cataract", "ophthalm", "pleurisy", "squint"]

def get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            if i == retries - 1:
                print("  FAILED", url, e); return None
            time.sleep(2)

def get_json(url):
    t = get(url); return json.loads(t) if t else None

def slugify(s):
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:80] or "untitled"

def clean_html_to_md(h):
    soup = BeautifulSoup(h, "html.parser")
    for t in soup(["script", "style", "iframe", "noscript", "svg", "form", "button"]):
        t.decompose()
    text = md(str(soup), heading_style="ATX", strip=["img"])
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\\([_*#\-.])", r"\1", text)      # unescape markdownify escapes
    # drop the site navigation menu that Elementor injects into content
    text = re.sub(r"(^\* \[[^\]]+\]\(https://(marcdeller\.com/[a-z0-9-]*/?|mdeller\.com/?)\)\n?){5,}", "", text, flags=re.M)
    # very long links (RCSB queries etc.): keep the anchor text only
    text = re.sub(r"\[([^\]]+)\]\([^)]{180,}\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def frontmatter(**kw):
    lines = ["---"]
    for k, v in kw.items():
        if v not in (None, ""):
            lines.append(f'{k}: "{str(v).replace(chr(34), chr(39))}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"

def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

# ---------------------------------------------------------------- WordPress
def fetch_wordpress():
    cats = {c["id"]: c["slug"] for c in get_json(f"{SITE}/wp-json/wp/v2/categories?per_page=100&_fields=id,slug")}
    github_repos = set()
    n_pages = n_posts = 0

    pages = get_json(f"{SITE}/wp-json/wp/v2/pages?per_page=100&_fields=id,title,link,slug,content,modified,status")
    for p in pages or []:
        if p["slug"] in ("test", "blog") or p.get("status") != "publish":
            continue
        title = html.unescape(re.sub("<[^>]+>", "", p["title"]["rendered"])).strip()
        body = clean_html_to_md(p["content"]["rendered"])
        github_repos.update(re.findall(rf"github\.com/{GITHUB_USER}/([A-Za-z0-9_-]+)", p["content"]["rendered"]))
        if len(body) < 200:
            continue  # Elementor pages whose content lives in widgets
        doc = frontmatter(title=f"marcdeller.com: {title}", type="webpage", year=p["modified"][:4],
                          authors="Deller, M.C.", venue="marcdeller.com", url=p["link"],
                          marc_role="author", source_file=f"wordpress page {p['slug']}") + f"# {title}\n\n{body}\n"
        write(os.path.join(HERE, "corpus", "web", "pages", f"{p['slug']}.md"), doc)
        n_pages += 1

    page = 1
    while True:
        posts = get_json(f"{SITE}/wp-json/wp/v2/posts?per_page=100&page={page}&_fields=id,title,link,slug,date,content,categories,excerpt")
        if not posts or (isinstance(posts, dict) and posts.get("code")):
            break
        for p in posts:
            title = html.unescape(re.sub("<[^>]+>", "", p["title"]["rendered"])).strip()
            body = clean_html_to_md(p["content"]["rendered"])
            github_repos.update(re.findall(rf"github\.com/{GITHUB_USER}/([A-Za-z0-9_-]+)", p["content"]["rendered"]))
            cslugs = [cats.get(c, str(c)) for c in p["categories"]]
            body = re.sub(r"^# .*\n+", "", body, count=1) if body.startswith("# ") else body
            if len(body) < 150:
                continue
            doc = frontmatter(title=title, type="blogpost", year=p["date"][:4], authors="Deller, M.C.",
                              venue="marcdeller.com blog", url=p["link"], categories=", ".join(cslugs),
                              marc_role="author", source_file=f"wordpress post {p['id']}") + \
                  f"# {title}\n\n*Published {p['date'][:10]} on marcdeller.com (categories: {', '.join(cslugs)}).*\n\n{body}\n"
            write(os.path.join(HERE, "corpus", "web", "posts", f"{p['date'][:10]}-{slugify(title)}.md"), doc)
            n_posts += 1
        if len(posts) < 100:
            break
        page += 1
    print(f"WordPress: {n_pages} pages, {n_posts} posts")
    return sorted(github_repos)

# ---------------------------------------------------------------- GitHub READMEs
def fetch_github(repos):
    n = 0
    for repo in repos:
        txt = None
        for branch in ("main", "master"):
            txt = get(f"https://raw.githubusercontent.com/{GITHUB_USER}/{repo}/{branch}/README.md", retries=1)
            if txt and not txt.startswith("404"):
                break
        if not txt or txt.strip() == "404: Not Found":
            print("  no README:", repo); continue
        txt = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", txt)       # drop images/badges
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        doc = frontmatter(title=f"GitHub README: {repo}", type="readme", year=time.strftime("%Y"),
                          authors="Deller, M.C.", venue=f"github.com/{GITHUB_USER}/{repo}",
                          url=f"https://github.com/{GITHUB_USER}/{repo}", marc_role="author and developer",
                          source_file=f"github {repo} README.md") + txt.strip() + "\n"
        write(os.path.join(HERE, "corpus", "projects", f"readme-{repo.lower()}.md"), doc)
        n += 1
    print(f"GitHub: {n} READMEs from {len(repos)} repos")

# ---------------------------------------------------------------- Europe PMC abstracts
def fetch_europepmc():
    q = urllib.parse.quote(EPMC_QUERY)
    res = get_json(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}&format=json&pageSize=200&resultType=core")
    items = []
    for r in (res or {}).get("resultList", {}).get("result", []):
        try: year = int(r.get("pubYear", 0))
        except ValueError: year = 0
        title = r.get("title", "").rstrip(".")
        if year < 1999 or any(w in title.lower() for w in EXCLUDE_TITLE_WORDS):
            continue
        items.append(dict(title=title, year=year, journal=r.get("journalTitle") or r.get("journalInfo", {}).get("journal", {}).get("title", ""),
                          doi=r.get("doi", ""), pmid=r.get("pmid", ""), authors=r.get("authorString", ""),
                          abstract=re.sub(r"<[^>]+>", "", r.get("abstractText", "") or "").strip()))
    items.sort(key=lambda x: (-x["year"], x["title"]))
    L = [frontmatter(title="Abstracts of Marc C. Deller's publications", type="reference", year=time.strftime("%Y"),
                     authors="various (Deller, M.C. co-author)", venue="Europe PMC", marc_role="author or co-author",
                     source_file="Europe PMC REST API"),
         "# Abstracts of Marc C. Deller's publications\n",
         f"Abstracts for {len(items)} of Marc Deller's papers indexed in Europe PMC (author search \"Deller MC\"), newest first. Marc's roles range from first and corresponding author (oncostatin M, TrkA gating ring, crystal harvesting, protein stability) to co-author contributing crystallography (JCSG structural genomics papers 2007–2012, HIV-1 Env and HCV E2 papers with Ian Wilson's lab, Stanford papers, and Incyte medicinal-chemistry papers).\n"]
    for x in items:
        L.append(f"## {x['title']} ({x['year']})\n")
        L.append(f"**Authors:** {x['authors']}  \n**Journal:** {x['journal']} ({x['year']}). " +
                 (f"DOI: {x['doi']}. " if x['doi'] else "") + (f"PMID: {x['pmid']}." if x['pmid'] else "") + "\n")
        L.append((x["abstract"] or "*(no abstract available)*") + "\n")
    write(os.path.join(HERE, "corpus", "reference", "publication-abstracts.md"), "\n".join(L))
    print(f"Europe PMC: {len(items)} abstracts")

# ---------------------------------------------------------------- generic HTML page -> markdown
def page_to_md(url, drop_selectors=("nav", "header", "footer", "script", "style", "noscript", "svg", "form", "button", "iframe")):
    h = get(url, retries=2)
    if not h:
        return None, None
    soup = BeautifulSoup(h, "html.parser")
    title = (soup.title.string.strip() if soup.title and soup.title.string else url)
    for sel in drop_selectors:
        for t in soup.find_all(sel):
            t.decompose()
    body = soup.find("main") or soup.body or soup
    text = md(str(body), heading_style="ATX", strip=["img"])
    text = re.sub(r"\\([_*#\-.])", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]{180,}\)", r"\1", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text

# ---------------------------------------------------------------- mdeller.com + app subdomains
def fetch_mdeller():
    h = get("https://mdeller.com")
    if not h:
        return []
    repos = set(re.findall(rf"github\.com/{GITHUB_USER}/([A-Za-z0-9_-]+)", h))
    apps = sorted(set(re.findall(r"https://([a-z0-9-]+)\.mdeller\.com", h)))
    title, text = page_to_md("https://mdeller.com")
    write(os.path.join(HERE, "corpus", "web", "mdeller", "mdeller-com-landing.md"),
          frontmatter(title="mdeller.com: Marc Deller's web app server (landing page)", type="webpage", year=time.strftime("%Y"),
                      authors="Deller, M.C.", venue="mdeller.com", url="https://mdeller.com", marc_role="author and developer",
                      source_file="https://mdeller.com") + "# mdeller.com — Marc Deller's web apps\n\n" + text + "\n")
    n = 1
    for app in apps:
        parts = []
        for path in ("/", "/about", "/home", "/stats"):
            t, txt = page_to_md(f"https://{app}.mdeller.com{path}")
            if txt and len(txt) > 200 and txt not in parts:
                parts.append(f"## {app}.mdeller.com{path}\n\n{txt}")
        if not parts:
            print("  no text:", app); continue
        write(os.path.join(HERE, "corpus", "web", "mdeller", f"app-{app}.md"),
              frontmatter(title=f"{app}.mdeller.com: web app pages", type="webapp", year=time.strftime("%Y"),
                          authors="Deller, M.C.", venue="mdeller.com", url=f"https://{app}.mdeller.com",
                          marc_role="author and developer", source_file=f"https://{app}.mdeller.com") +
              f"# {app}.mdeller.com\n\n" + "\n\n".join(parts) + "\n")
        n += 1
    print(f"mdeller.com: {n} documents; repos found: {sorted(repos)}")
    return sorted(repos)

# ---------------------------------------------------------------- Squarespace company sites
def fetch_site(base, name, skip=("terms", "privacy", "contact")):
    sm = get(f"{base}/sitemap.xml")
    urls = re.findall(r"<loc>([^<]+)</loc>", sm or "") or [base]
    n = 0
    for u in urls:
        if any(k in u for k in skip):
            continue
        title, text = page_to_md(u)
        if not text or len(text) < 100:
            continue
        slug = slugify(u.rstrip("/").split("/")[-1] or "home")
        write(os.path.join(HERE, "corpus", "web", name, f"{slug}.md"),
              frontmatter(title=f"{name}: {title}", type="webpage", year=time.strftime("%Y"), authors=name,
                          venue=base, url=u, marc_role="company Marc co-founded (Elora) or advises (DeepCovalent)",
                          source_file=u) + f"# {title}\n\n{text}\n")
        n += 1
    print(f"{name}: {n} pages")

# ---------------------------------------------------------------- ORCID public API
def fetch_orcid(orcid="0000-0001-8070-6502"):
    base = f"https://pub.orcid.org/v3.0/{orcid}"
    hdr = {"Accept": "application/json", **UA}
    def gj(path):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=hdr), timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print("  orcid failed", path, e); return {}
    person = gj("/person"); works = gj("/works"); emp = gj("/employments"); edu = gj("/educations")
    L = [frontmatter(title="ORCID record of Marc C. Deller", type="reference", year=time.strftime("%Y"), authors="Deller, M.C.",
                     venue="ORCID", url=f"https://orcid.org/{orcid}", marc_role="subject", source_file="ORCID public API v3.0"),
         f"# ORCID record of Marc C. Deller ({orcid})\n"]
    kw = [k["content"] for k in (person.get("keywords", {}).get("keyword") or [])]
    if kw: L.append("Keywords: " + ", ".join(kw) + "\n")
    for label, d in (("Employment", emp), ("Education", edu)):
        rows = []
        for g in d.get("affiliation-group", []):
            for s in g.get("summaries", []):
                k = [v for v in s.values() if isinstance(v, dict)][0]
                org = k.get("organization", {}); sd = k.get("start-date") or {}; ed = k.get("end-date") or {}
                y = lambda x: (x or {}).get("year", {}).get("value", "") if x else ""
                rows.append(f"- {k.get('role-title','')} — {org.get('name','')}, {org.get('address',{}).get('city','')} ({y(sd)}–{y(ed) or 'present'})")
        if rows: L.append(f"## {label}\n\n" + "\n".join(rows) + "\n")
    items = []
    for g in works.get("group", []):
        s = g["work-summary"][0]
        title = s["title"]["title"]["value"]; year = (s.get("publication-date") or {}).get("year", {}) or {}
        doi = next((e["external-id-value"] for e in s.get("external-ids", {}).get("external-id", []) if e["external-id-type"] == "doi"), "")
        items.append((year.get("value", ""), title, (s.get("journal-title") or {}).get("value", ""), s.get("type", ""), doi))
    items.sort(reverse=True)
    L.append(f"## Works ({len(items)})\n\n" + "\n".join(f"- {t} ({y}). {j}. {k}." + (f" DOI: {d}" if d else "") for y, t, j, k, d in items) + "\n")
    write(os.path.join(HERE, "corpus", "reference", "orcid-record.md"), "\n".join(L))
    print(f"ORCID: {len(items)} works")

# ---------------------------------------------------------------- Google Scholar profile (single public page)
def fetch_scholar(user="o02oSJIAAAAJ"):
    h = get(f"https://scholar.google.com/citations?user={user}&hl=en&pagesize=100", retries=1)
    if not h or "gsc_a_tr" not in h:
        print("Scholar: not available"); return
    soup = BeautifulSoup(h, "html.parser")
    stats = [td.get_text() for td in soup.select("td.gsc_rsb_std")]
    rows = []
    for tr in soup.select("tr.gsc_a_tr"):
        t = tr.select_one("a.gsc_a_at"); gs = tr.select("div.gs_gray"); c = tr.select_one("a.gsc_a_ac"); y = tr.select_one("span.gsc_a_h")
        rows.append((t.get_text() if t else "", gs[0].get_text() if gs else "", gs[1].get_text() if len(gs) > 1 else "",
                     c.get_text() if c else "0", y.get_text() if y else ""))
    L = [frontmatter(title="Google Scholar profile of Marc C. Deller", type="reference", year=time.strftime("%Y"), authors="Deller, M.C.",
                     venue="Google Scholar", url=f"https://scholar.google.com/citations?user={user}", marc_role="subject",
                     source_file="Google Scholar profile page"),
         "# Google Scholar profile of Marc C. Deller\n"]
    if len(stats) >= 6:
        L.append(f"Citation statistics (all time / since {int(time.strftime('%Y'))-5}): citations {stats[0]} / {stats[1]}; h-index {stats[2]} / {stats[3]}; i10-index {stats[4]} / {stats[5]}.\n")
    L.append(f"## Publications listed ({len(rows)}), by citation count\n")
    for t, a, v, c, y in rows:
        L.append(f"- {t} ({y}). {a}. {v}. Cited by {c}.")
    write(os.path.join(HERE, "corpus", "reference", "google-scholar-profile.md"), "\n".join(L) + "\n")
    print(f"Scholar: {len(rows)} publications, stats {stats[:6]}")

# ---------------------------------------------------------------- RCSB PDB (search + data API)
def fetch_rcsb():
    q = {"query": {"type": "group", "logical_operator": "or", "nodes": [
            {"type": "terminal", "service": "text", "parameters": {"attribute": "citation.rcsb_authors", "operator": "exact_match", "value": "Deller, M.C."}},
            {"type": "terminal", "service": "text", "parameters": {"attribute": "rcsb_primary_citation.rcsb_authors", "operator": "exact_match", "value": "Deller, M."}},
            {"type": "terminal", "service": "text", "parameters": {"attribute": "audit_author.name", "operator": "exact_match", "value": "Deller, M.C."}}]},
         "return_type": "entry", "request_options": {"results_content_type": ["experimental"], "paginate": {"start": 0, "rows": 1000},
         "sort": [{"sort_by": "rcsb_accession_info.initial_release_date", "direction": "desc"}]}}
    url = "https://search.rcsb.org/rcsbsearch/v2/query?json=" + urllib.parse.quote(json.dumps(q))
    res = get_json(url)
    ids = [r["identifier"] for r in (res or {}).get("result_set", [])]
    rows = []
    for pid in ids:
        x = get_json(f"https://data.rcsb.org/rest/v1/core/entry/{pid}")
        if not x: continue
        info = x.get("rcsb_entry_info", {}); cit = x.get("rcsb_primary_citation", {}) or {}
        res_ = info.get("resolution_combined") or []
        rows.append(dict(id=pid, title=x.get("struct", {}).get("title", ""), method=", ".join(m["method"] for m in x.get("exptl", [])),
                         res=res_[0] if res_ else None, date=(x.get("rcsb_accession_info", {}).get("initial_release_date", ""))[:10],
                         authors=", ".join(a["name"] for a in x.get("audit_author", [])), cit_title=cit.get("title"), cit_year=cit.get("year"),
                         doi=cit.get("pdbx_database_id_DOI"), journal=cit.get("journal_abbrev"), kw=x.get("struct_keywords", {}).get("pdbx_keywords", ""),
                         sg=x.get("symmetry", {}).get("space_group_name_H_M", ""), mw=info.get("molecular_weight"), nres=info.get("deposited_polymer_monomer_count"),
                         ligs=", ".join(info.get("nonpolymer_bound_components", []) or [])))
    rows.sort(key=lambda r: r["date"], reverse=True)
    L = [frontmatter(title="PDB structures deposited by Marc C. Deller", type="reference", year=time.strftime("%Y"), authors="Deller, M.C. and co-depositors",
                     venue="RCSB Protein Data Bank", url="https://marcdeller.com/structures/", marc_role="deposition author or citation author",
                     source_file="RCSB search + data API"),
         "# PDB structures deposited by Marc C. Deller\n",
         f"This list holds the {len(rows)} Protein Data Bank entries on which Marc C. Deller is a deposition author or an author of the primary citation, retrieved live from the RCSB PDB on {time.strftime('%d %B %Y')}. It excludes the several hundred structural-genomics entries deposited under the collective 'Joint Center for Structural Genomics (JCSG)' author name during his years as JCSG Target Manager (2006–2015); across his career he has contributed to more than 400 PDB depositions. A browsable list is at https://marcdeller.com/structures/.\n",
         "## Entries (newest release first)\n"]
    for r in rows:
        res_ = f"{r['res']} Å" if r["res"] else "n/a"
        cit = f" Primary citation: {r['cit_title']} ({r['journal']}, {r['cit_year']}), DOI {r['doi']}." if r["cit_title"] else ""
        lig = f" Bound ligands: {r['ligs']}." if r["ligs"] else ""
        L.append(f"- **{r['id']}** — {r['title']}. Method: {r['method']}; resolution {res_}; space group {r['sg']}; {r['nres']} polymer residues, {r['mw']} kDa; released {r['date']}. Deposition authors: {r['authors']}. Keywords: {r['kw']}.{lig}{cit}")
    write(os.path.join(HERE, "corpus", "reference", "pdb-structures.md"), "\n".join(L) + "\n")
    print(f"RCSB: {len(rows)} entries")

if __name__ == "__main__":
    which = sys.argv[1:] or ["wordpress", "mdeller", "github", "sites", "europepmc", "orcid", "scholar", "rcsb"]
    repos = set()
    if "wordpress" in which: repos |= set(fetch_wordpress())
    if "mdeller" in which: repos |= set(fetch_mdeller())
    if "github" in which: fetch_github(sorted(repos))
    if "sites" in which:
        fetch_site("https://www.eloratherapeutics.com", "eloratherapeutics")
        fetch_site("https://www.deepcovalent.com", "deepcovalent")
    if "europepmc" in which: fetch_europepmc()
    if "orcid" in which: fetch_orcid()
    if "scholar" in which: fetch_scholar()
    if "rcsb" in which: fetch_rcsb()
