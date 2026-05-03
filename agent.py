"""
Mental Health Science Agent
Fetches latest research findings → AI digests → DeSci project match → Social-ready posts
Sources: OpenAlex, medRxiv, ScienceDaily RSS
Output: Telegram message + Discord embed + saved JSON
"""

import os
import json
import time
import hashlib
import requests
import feedparser
from datetime import datetime, timedelta
from typing import Optional
from config import CONFIG
from desci_matcher import match_desci_projects, format_desci_section_telegram, format_desci_section_discord


# ─── SOURCES ────────────────────────────────────────────────────────────────

OPENALEX_URL        = "https://api.openalex.org/works"
MEDRXIV_API_URL     = "https://api.biorxiv.org/details/medrxiv"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
ALTMETRIC_URL       = "https://api.altmetric.com/v1/doi"

SEARCH_QUERIES = [
    # Depression - food/diet
    "depression omega-3 randomized controlled trial",
    "depression gut microbiome diet intervention",
    "depression exercise physical activity meta-analysis",
    # Grief / pet loss
    "prolonged grief disorder treatment intervention",
    "pet loss bereavement grief therapy",
    "complicated grief intervention randomized",
    # Mental health - lifestyle
    "depression nature exposure green space mental health",
    "social connection loneliness depression intervention",
    "sleep depression treatment lifestyle",
    "mindfulness meditation depression anxiety clinical trial",
]

SCIENCEDAILY_FEEDS = [
    "https://www.sciencedaily.com/rss/mind_brain/depression.xml",
    "https://www.sciencedaily.com/rss/mind_brain/grief.xml",
    "https://www.sciencedaily.com/rss/mind_brain/mental_health.xml",
]

# Keywords used to filter medRxiv preprints
MEDRXIV_KEYWORDS = [
    "depression", "anxiety", "grief", "mental health", "mindfulness",
    "psychotherapy", "loneliness", "sleep disorder", "psychiatric",
]


# ─── FETCH: OPENALEX ─────────────────────────────────────────────────────────

def reconstruct_abstract(inverted_index: dict) -> str:
    """Reconstruct plain text from OpenAlex inverted-index abstract format."""
    if not inverted_index:
        return ""
    positions: dict[int, str] = {}
    for word, pos_list in inverted_index.items():
        for pos in pos_list:
            positions[pos] = word
    return " ".join(positions[i] for i in sorted(positions))


def fetch_openalex(query: str, max_results: int = 5) -> list[dict]:
    """Fetch most recent works from OpenAlex by keyword search."""
    params = {
        "search": query,
        "filter": "cited_by_count:>2",
        "per-page": max_results,
        "sort": "publication_date:desc",
        "select": "id,title,abstract_inverted_index,publication_date,doi,primary_location,cited_by_count,type",
    }
    headers = {"User-Agent": "SciSignal/1.0 (mailto:contact@scisignal.app)"}

    try:
        r = requests.get(OPENALEX_URL, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        if not results:
            print(f"  OpenAlex 0 results for query='{query[:40]}'")
            print(f"  Full response: {json.dumps(data, indent=2)[:1000]}")
    except Exception as e:
        print(f"  OpenAlex error (query='{query[:40]}'): {e}")
        return []

    EXCLUDE_TERMS = [
        'tumor', 'cancer', 'oncology', 'neonatal',
        'latin', 'virgil', 'aeneid', 'surgery', 'cardiac',
        'osteoarthritis', 'outdoor', 'climate', 'cardiovascular',
        'wearable', 'teacher', 'school', 'pediatric', 'infant',
        'pregnancy', 'obstetric',
        'window', 'colitis', 'copd', 'lung', 'pornograph',
        'sexual behavior', 'ulcerative', 'glazed', 'lifecycle', 'assessment',
        'bowel', 'digestive', 'colonic', 'intestinal', 'gastro',
        'nursing education', 'reproductive',
    ]

    papers = []
    for work in results:
        title_raw = work.get("title") or ""
        title_lower = title_raw.lower()
        if any(term in title_lower for term in EXCLUDE_TERMS):
            continue

        abstract = reconstruct_abstract(work.get("abstract_inverted_index") or {})
        if len(abstract) < 100:
            continue

        doi_raw = work.get("doi") or ""
        doi = doi_raw.replace("https://doi.org/", "").strip() or None

        primary = work.get("primary_location") or {}
        url = primary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else "")

        pub_date = work.get("publication_date") or ""
        papers.append({
            "source": "OpenAlex",
            "id": (work.get("id") or "").split("/")[-1],
            "title": title_raw,
            "abstract": abstract[:3000],
            "year": pub_date[:4],
            "url": url,
            "doi": doi,
            "citations": work.get("cited_by_count") or 0,
            "query": query,
        })

    return papers


# ─── FETCH: MEDRXIV ──────────────────────────────────────────────────────────

def fetch_medrxiv(max_results: int = 20) -> list[dict]:
    """Fetch recent medRxiv preprints and filter by relevance keywords."""
    from_date = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    to_date   = datetime.now().strftime("%Y-%m-%d")

    try:
        r = requests.get(
            f"{MEDRXIV_API_URL}/{from_date}/{to_date}/0",
            timeout=15,
        )
        r.raise_for_status()
        collection = r.json().get("collection", [])
    except Exception as e:
        print(f"  medRxiv error: {e}")
        return []

    kw_lower = [k.lower() for k in MEDRXIV_KEYWORDS]
    papers = []

    for item in collection:
        title    = item.get("title", "")
        abstract = item.get("abstract", "")
        text     = (title + " " + abstract).lower()

        if not any(kw in text for kw in kw_lower):
            continue
        if len(abstract) < 100:
            continue

        doi = item.get("doi", "") or ""
        uid = hashlib.md5(doi.encode() if doi else title.encode()).hexdigest()[:10]

        papers.append({
            "source": "medRxiv",
            "id": uid,
            "title": title,
            "abstract": abstract[:3000],
            "year": (item.get("date") or "")[:4],
            "url": f"https://doi.org/{doi}" if doi else "",
            "doi": doi or None,
            "citations": 0,
            "query": "medrxiv",
        })

        if len(papers) >= max_results:
            break

    return papers


# ─── FETCH: SCIENCEDAILY RSS ─────────────────────────────────────────────────

def fetch_sciencedaily() -> list[dict]:
    """Fetch recent plain-English science summaries from ScienceDaily RSS."""
    import re
    articles = []
    cutoff = datetime.now() - timedelta(days=CONFIG["lookback_days"])

    for feed_url in SCIENCEDAILY_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                pub_date = (
                    datetime(*entry.published_parsed[:6])
                    if hasattr(entry, "published_parsed") and entry.published_parsed
                    else datetime.now()
                )
                if pub_date < cutoff:
                    continue
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", "")).strip()
                if summary and len(summary) > 80:
                    articles.append({
                        "source": "ScienceDaily",
                        "id": hashlib.md5(entry.link.encode()).hexdigest()[:10],
                        "title": entry.title,
                        "abstract": summary[:2000],
                        "year": str(pub_date.year),
                        "url": entry.link,
                        "doi": None,
                        "citations": 0,
                        "query": feed_url.split("/")[-1].replace(".xml", ""),
                    })
        except Exception as e:
            print(f"  ScienceDaily error ({feed_url}): {e}")

    return articles


# ─── LEGITIMACY ENRICHMENT ───────────────────────────────────────────────────

def enrich_legitimacy(paper: dict) -> dict:
    """
    Enrich a paper dict with:
      - h_index: first-author H-index from Semantic Scholar
      - altmetric_score: Altmetric attention score (requires DOI)
    Citation count is updated in-place if Semantic Scholar finds the paper.
    """
    title = paper.get("title", "")
    doi   = paper.get("doi")

    h_index        = None
    altmetric_score = None

    # ── Semantic Scholar: disabled — re-enable once we have an API key ──

    # ── Altmetric: attention score ──
    if doi:
        try:
            r = requests.get(f"{ALTMETRIC_URL}/{doi}", timeout=10)
            if r.status_code == 200:
                altmetric_score = r.json().get("score")
            time.sleep(0.3)
        except Exception as e:
            print(f"  Altmetric error ({doi}): {e}")

    paper["h_index"]         = h_index
    paper["altmetric_score"] = altmetric_score
    return paper


# ─── AI DIGEST ───────────────────────────────────────────────────────────────

DIGEST_SYSTEM_PROMPT = """You are a science communicator who specializes in mental health research.
Your job is to read dense academic abstracts and extract the ONE most interesting, actionable finding —
then rewrite it in two formats: one for a curious non-scientist, one for social media.

Always be accurate. Never exaggerate. If the evidence is weak (small sample, preliminary), say so briefly.
If the study found NO significant effect, that's also interesting — report it honestly.

Respond ONLY in valid JSON. No markdown, no backticks, no preamble."""

DIGEST_USER_PROMPT = """Here is a scientific paper with legitimacy signals. Extract the key finding and score it.

TITLE: {title}
SOURCE: {source} ({year})
ABSTRACT:
{abstract}

LEGITIMACY SIGNALS:
- First-author H-index: {h_index}
- Citation count: {citations}
- Altmetric attention score: {altmetric_score}

Apply these scoring adjustments to your relevance_score:
  +1 if H-index > 20
  +1 if citation count > 5
  +2 if Altmetric score > 50
  -5 if this is a study protocol (pre-registered plan, no results yet)
  -2 if sample size n < 30 participants
Base your score on scientific quality and actionability (1–10), then apply adjustments, clamping the final value between 1 and 10.

Respond with this exact JSON structure:
{{
  "category": one of ["Food/Diet", "Exercise", "Therapy", "Nature", "Social", "Sleep", "App/Digital", "Substance", "Other"],
  "key_finding": "1-2 sentences. The actual finding in plain English. What works, for whom, how much.",
  "why_it_matters": "1 sentence. Why should a non-scientist care about this?",
  "actionable_tip": "1 sentence starting with a verb. What can someone actually DO with this info?",
  "evidence_strength": one of ["Strong (RCT/Meta-analysis)", "Moderate (Clinical study)", "Preliminary (Small/Observational)"],
  "social_caption": "A punchy, engaging 2-3 sentence social media caption. No hashtags yet. No emojis unless they add meaning. Start with the hook.",
  "hashtags": ["list", "of", "5-8", "relevant", "hashtags", "without", "the", "#"],
  "relevance_score": integer 1-10 after adjustments,
  "skip_reason": "If relevance_score < 6, briefly explain why. Otherwise empty string."
}}"""


def digest_paper(paper: dict) -> Optional[dict]:
    """Send paper to OpenRouter for digestion into social-ready content."""
    prompt = DIGEST_USER_PROMPT.format(
        title          = paper["title"],
        source         = paper["source"],
        year           = paper.get("year", "n/d"),
        abstract       = paper["abstract"],
        h_index        = paper.get("h_index") if paper.get("h_index") is not None else "unknown",
        citations      = paper.get("citations", 0),
        altmetric_score = paper.get("altmetric_score") if paper.get("altmetric_score") is not None else "not indexed",
    )

    try:
        print(f"  [digest_paper] API key prefix: {CONFIG['openrouter_api_key'][:10]}")
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {CONFIG['openrouter_api_key']}",
                "Content-Type": "application/json",
            },
            json={
                "model": "meta-llama/llama-3.3-70b-instruct",
                "messages": [
                    {"role": "system", "content": DIGEST_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
            },
            timeout=30,
        )
        resp_json = response.json()
        if "choices" not in resp_json:
            print(f"  OpenRouter missing 'choices' key. Full response: {json.dumps(resp_json, indent=2)}")
            return None
        raw = resp_json["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        digest = json.loads(raw)
        digest["paper"] = {
            "title"    : paper["title"],
            "source"   : paper["source"],
            "url"      : paper["url"],
            "year"     : paper.get("year", ""),
            "citations": paper.get("citations"),
            "doi"      : paper.get("doi"),
        }
        return digest
    except Exception as e:
        print(f"  Digest error for '{paper['title'][:50]}...': {e}")
        return None


# ─── DEDUPLICATION ───────────────────────────────────────────────────────────

def load_seen_ids(path: str) -> set:
    try:
        with open(path) as f:
            return set(json.load(f))
    except:
        return set()

def save_seen_ids(ids: set, path: str):
    with open(path, "w") as f:
        json.dump(list(ids), f)


# ─── TELEGRAM SENDER ─────────────────────────────────────────────────────────

def html_escape(text: str) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_telegram(digest: dict, bot_token: str, chat_id: str):
    """Format and send a digest as a Telegram message."""
    p = digest["paper"]
    d = digest

    strength_emoji = {
        "Strong (RCT/Meta-analysis)": "💪",
        "Moderate (Clinical study)": "🔬",
        "Preliminary (Small/Observational)": "🌱",
    }.get(d.get("evidence_strength", ""), "📄")

    category_emoji = {
        "Food/Diet": "🥗", "Exercise": "🏃", "Therapy": "🧠",
        "Nature": "🌿", "Social": "👥", "Sleep": "😴",
        "App/Digital": "📱", "Substance": "💊", "Other": "📌",
    }.get(d.get("category", "Other"), "📌")

    hashtags = " ".join(f"#{html_escape(h)}" for h in d.get("hashtags", []))

    desci_section = ""
    if d.get("desci"):
        desci_section = format_desci_section_telegram(d["desci"])

    desci_tags = ""
    if d.get("desci", {}).get("matches"):
        desci_tags = "#DeSci #Web3Science #DecentralizedScience"

    message = f"""{category_emoji} <b>{html_escape(d.get('category', 'Research'))}</b> {strength_emoji}

{html_escape(d.get('social_caption', ''))}

💡 <b>Tip:</b> {html_escape(d.get('actionable_tip', ''))}

📊 Evidence: <i>{html_escape(d.get('evidence_strength', ''))}</i>
🔗 <a href="{html_escape(p['url'])}">Read the study</a> • {html_escape(p['source'])} {html_escape(p['year'])}{desci_section}

{hashtags} {desci_tags}""".strip()

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=30)
        if r.status_code == 200:
            print(f"  ✅ Sent to Telegram: {p['title'][:60]}...")
        else:
            print(f"  ❌ Telegram error: {r.text}")
    except Exception as e:
        print(f"  ❌ Telegram request failed: {e}")


# ─── DISCORD SENDER ──────────────────────────────────────────────────────────

def send_discord(digest: dict, webhook_url: str):
    """Format and send a digest as a Discord embed."""
    p = digest["paper"]
    d = digest

    category_emoji = {
        "Food/Diet": "🥗", "Exercise": "🏃", "Therapy": "🧠",
        "Nature": "🌿", "Social": "👥", "Sleep": "😴",
        "App/Digital": "📱", "Substance": "💊", "Other": "📌",
    }.get(d.get("category", "Other"), "📌")

    hashtags  = " ".join(f"#{h}" for h in d.get("hashtags", []))
    desci_tags = "#DeSci #Web3Science" if d.get("desci", {}).get("matches") else ""

    fields = [
        {"name": "💡 Actionable Tip",    "value": d.get("actionable_tip", ""),    "inline": False},
        {"name": "📊 Evidence Strength", "value": d.get("evidence_strength", ""), "inline": True},
        {"name": "🏷️ Hashtags",          "value": f"{hashtags} {desci_tags}".strip(), "inline": False},
    ]

    if d.get("desci"):
        desci_fields = format_desci_section_discord(d["desci"])
        if desci_fields:
            fields.append({"name": "─" * 30, "value": "🔬 **DeSci Projects On This**", "inline": False})
            fields.extend(desci_fields)

    embed = {
        "title":       f"{category_emoji} {d.get('category', 'Research')} | {p['source']} {p['year']}",
        "description": d.get("social_caption", ""),
        "color":       0x00C9A7,
        "fields":      fields,
        "footer":      {"text": p["title"][:100]},
        "url":         p["url"],
    }
    try:
        r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=30)
        if r.status_code in (200, 204):
            print(f"  ✅ Sent to Discord: {p['title'][:60]}...")
        else:
            print(f"  ❌ Discord error: {r.text}")
    except Exception as e:
        print(f"  ❌ Discord request failed: {e}")


# ─── MAIN PIPELINE ───────────────────────────────────────────────────────────

def run():
    print(f"\n{'='*60}")
    print(f"  Mental Health Science Agent — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    seen_path   = "seen_ids.json"
    output_path = f"digests_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    seen_ids    = load_seen_ids(seen_path)
    all_papers  = []

    # ── 1. Fetch from all sources ──
    print("📡 Fetching papers...\n")

    import random
    sampled_queries = random.sample(SEARCH_QUERIES, min(4, len(SEARCH_QUERIES)))

    # OpenAlex (replaces PubMed + Semantic Scholar fetches)
    for query in sampled_queries:
        print(f"  OpenAlex: {query}")
        papers = fetch_openalex(query, max_results=5)
        all_papers.extend(papers)
        time.sleep(1.0)  # OpenAlex polite crawling

    # medRxiv recent preprints filtered by keywords
    print(f"  medRxiv: last 14 days filtered by keywords...")
    medrxiv_papers = fetch_medrxiv(max_results=15)
    all_papers.extend(medrxiv_papers)
    print(f"    → {len(medrxiv_papers)} relevant preprints")

    # ScienceDaily RSS
    print(f"  ScienceDaily RSS feeds...")
    sd_articles = fetch_sciencedaily()
    all_papers.extend(sd_articles)

    print(f"\n  → {len(all_papers)} total papers fetched")

    # ── 2. Deduplicate ──
    import re as _re
    def _norm_title(t: str) -> str:
        return _re.sub(r'[^a-z0-9 ]', '', t.lower()).strip()

    fresh_papers = []
    seen_titles: set[str] = set()
    for p in all_papers:
        uid = hashlib.md5(f"{p['id']}{p['title']}".encode()).hexdigest()
        p["_uid"] = uid
        norm = _norm_title(p["title"])
        if uid in seen_ids or norm in seen_titles:
            continue
        seen_titles.add(norm)
        fresh_papers.append(p)

    print(f"  → {len(fresh_papers)} new (after dedup)\n")

    if not fresh_papers:
        print("Nothing new today. Run again tomorrow!")
        return

    # ── 3. Legitimacy enrichment ──
    print("🔍 Enriching with legitimacy signals...\n")
    cap = CONFIG.get("max_papers_per_run", 10)
    for paper in fresh_papers[:cap]:
        print(f"  → {paper['title'][:65]}...")
        enrich_legitimacy(paper)
        h   = paper.get("h_index")
        alt = paper.get("altmetric_score")
        cit = paper.get("citations", 0)
        print(f"     H-index: {h}  |  Citations: {cit}  |  Altmetric: {alt}")

    # ── 4. Digest with LLM ──
    print("\n🧠 Digesting with LLM...\n")
    good_digests = []
    min_score    = CONFIG.get("min_relevance_score", 6)

    for paper in fresh_papers[:cap]:
        print(f"  → {paper['title'][:70]}...")
        digest = digest_paper(paper)
        time.sleep(0.5)

        if digest is None:
            continue

        score = digest.get("relevance_score", 0)
        if score < min_score:
            print(f"     ↳ Skipped (score {score}/10: {digest.get('skip_reason', '')})")
            continue

        print(f"     ✅ Score {score}/10 | {digest.get('category')} | {digest.get('evidence_strength')}")

        # DeSci matching
        print(f"     🔬 Matching DeSci projects...")
        desci_result = match_desci_projects(
            digest,
            api_key=CONFIG["openrouter_api_key"],
            projects_path=CONFIG.get("desci_projects_path", "desci_projects.json"),
        )
        digest["desci"] = desci_result
        matches = desci_result.get("matches", [])
        if matches:
            print(f"     🔗 Matched: {', '.join(m['name'] for m in matches)}")
        else:
            print(f"     ↳ No DeSci match ({desci_result.get('no_match_reason', 'no close match')})")

        time.sleep(0.3)
        good_digests.append(digest)
        seen_ids.add(paper["_uid"])

    print(f"\n  → {len(good_digests)} high-quality findings\n")

    # ── 5. Send notifications ──
    if good_digests:
        print("📬 Sending notifications...\n")
        for digest in good_digests:
            if CONFIG.get("telegram_bot_token") and CONFIG.get("telegram_chat_id"):
                send_telegram(digest, CONFIG["telegram_bot_token"], CONFIG["telegram_chat_id"])
                time.sleep(1)

            if CONFIG.get("discord_webhook_url"):
                send_discord(digest, CONFIG["discord_webhook_url"])
                time.sleep(1)

    # ── 6. Save output ──
    with open(output_path, "w") as f:
        json.dump(good_digests, f, indent=2)
    save_seen_ids(seen_ids, seen_path)

    print(f"\n✅ Done! Saved to {output_path}")
    print(f"   {len(good_digests)} findings sent\n")

    return good_digests


if __name__ == "__main__":
    run()
