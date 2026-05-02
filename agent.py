"""
Mental Health Science Agent
Fetches latest research findings → AI digests → DeSci project match → Social-ready posts
Sources: PubMed, Semantic Scholar, ScienceDaily RSS
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

PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
PUBMED_SUMMARY_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"

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


# ─── FETCH: PUBMED ───────────────────────────────────────────────────────────

def fetch_pubmed(query: str, max_results: int = 3) -> list[dict]:
    """Search PubMed and return paper metadata + abstracts."""
    two_years_ago = (datetime.now() - timedelta(days=730)).strftime("%Y/%m/%d")

    # Step 1: Search for IDs
    search_params = {
        "db": "pubmed",
        "term": query,
        "retmax": max_results,
        "sort": "date",
        "mindate": two_years_ago,
        "datetype": "pdat",
        "retmode": "json",
    }
    try:
        r = requests.get(PUBMED_SEARCH_URL, params=search_params, timeout=10)
        ids = r.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"  PubMed search error: {e}")
        return []

    if not ids:
        return []

    # Step 2: Fetch abstracts
    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    try:
        r = requests.get(PUBMED_FETCH_URL, params=fetch_params, timeout=15)
        xml = r.text
    except Exception as e:
        print(f"  PubMed fetch error: {e}")
        return []

    # Step 3: Also get titles + years via summary
    summary_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "retmode": "json",
    }
    titles_map = {}
    years_map = {}
    try:
        sr = requests.get(PUBMED_SUMMARY_URL, params=summary_params, timeout=10)
        result = sr.json().get("result", {})
        for pid in ids:
            entry = result.get(pid, {})
            titles_map[pid] = entry.get("title", "")
            years_map[pid] = entry.get("pubdate", "")[:4]
    except:
        pass

    # Step 4: Parse abstracts from XML (simple extraction)
    papers = []
    import re
    abstract_blocks = re.findall(
        r'<PubmedArticle>(.*?)</PubmedArticle>', xml, re.DOTALL
    )
    for i, (pid, block) in enumerate(zip(ids, abstract_blocks)):
        abstract_match = re.search(
            r'<AbstractText[^>]*>(.*?)</AbstractText>', block, re.DOTALL
        )
        abstract = abstract_match.group(1) if abstract_match else ""
        abstract = re.sub(r'<[^>]+>', '', abstract).strip()

        if abstract and len(abstract) > 100:
            papers.append({
                "source": "PubMed",
                "id": pid,
                "title": titles_map.get(pid, f"Study #{pid}"),
                "abstract": abstract[:3000],
                "year": years_map.get(pid, ""),
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                "query": query,
            })
    return papers


# ─── FETCH: SCIENCEDAILY RSS ─────────────────────────────────────────────────

def fetch_sciencedaily() -> list[dict]:
    """Fetch recent plain-English science summaries from ScienceDaily RSS."""
    articles = []
    cutoff = datetime.now() - timedelta(days=CONFIG["lookback_days"])

    for feed_url in SCIENCEDAILY_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                pub_date = datetime(*entry.published_parsed[:6]) if hasattr(entry, 'published_parsed') and entry.published_parsed else datetime.now()
                if pub_date < cutoff:
                    continue
                summary = entry.get("summary", "")
                import re
                summary = re.sub(r'<[^>]+>', '', summary).strip()
                if summary and len(summary) > 80:
                    articles.append({
                        "source": "ScienceDaily",
                        "id": hashlib.md5(entry.link.encode()).hexdigest()[:10],
                        "title": entry.title,
                        "abstract": summary[:2000],
                        "year": str(pub_date.year),
                        "url": entry.link,
                        "query": feed_url.split("/")[-1].replace(".xml", ""),
                    })
        except Exception as e:
            print(f"  ScienceDaily error ({feed_url}): {e}")

    return articles


# ─── FETCH: SEMANTIC SCHOLAR ─────────────────────────────────────────────────

def fetch_semantic_scholar(query: str, max_results: int = 2) -> list[dict]:
    """Fetch highly-cited recent papers from Semantic Scholar."""
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,year,citationCount,externalIds,url",
        "publicationDateOrYear": f"{datetime.now().year - 2}-",
    }
    try:
        r = requests.get(SEMANTIC_SCHOLAR_URL, params=params, timeout=10)
        data = r.json().get("data", [])
    except Exception as e:
        print(f"  Semantic Scholar error: {e}")
        return []

    papers = []
    for p in data:
        abstract = p.get("abstract") or ""
        if not abstract or len(abstract) < 100:
            continue
        pid = p.get("externalIds", {}).get("PubMed", p.get("paperId", ""))
        papers.append({
            "source": "SemanticScholar",
            "id": str(pid),
            "title": p.get("title", ""),
            "abstract": abstract[:3000],
            "year": str(p.get("year", "")),
            "url": p.get("url", ""),
            "citations": p.get("citationCount", 0),
            "query": query,
        })
    return papers


# ─── AI DIGEST ───────────────────────────────────────────────────────────────

DIGEST_SYSTEM_PROMPT = """You are a science communicator who specializes in mental health research.
Your job is to read dense academic abstracts and extract the ONE most interesting, actionable finding — 
then rewrite it in two formats: one for a curious non-scientist, one for social media.

Always be accurate. Never exaggerate. If the evidence is weak (small sample, preliminary), say so briefly.
If the study found NO significant effect, that's also interesting — report it honestly.

Respond ONLY in valid JSON. No markdown, no backticks, no preamble."""

DIGEST_USER_PROMPT = """Here is a scientific paper abstract. Extract and rewrite the key finding.

TITLE: {title}
SOURCE: {source} ({year})
ABSTRACT:
{abstract}

Respond with this exact JSON structure:
{{
  "category": one of ["Food/Diet", "Exercise", "Therapy", "Nature", "Social", "Sleep", "App/Digital", "Substance", "Other"],
  "key_finding": "1-2 sentences. The actual finding in plain English. What works, for whom, how much.",
  "why_it_matters": "1 sentence. Why should a non-scientist care about this?",
  "actionable_tip": "1 sentence starting with a verb. What can someone actually DO with this info?",
  "evidence_strength": one of ["Strong (RCT/Meta-analysis)", "Moderate (Clinical study)", "Preliminary (Small/Observational)"],
  "social_caption": "A punchy, engaging 2-3 sentence social media caption. No hashtags yet. No emojis unless they add meaning. Start with the hook — the most surprising or relatable part of the finding.",
  "hashtags": ["list", "of", "5-8", "relevant", "hashtags", "without", "the", "#"],
  "relevance_score": integer 1-10 where 10 = groundbreaking actionable finding, 1 = vague/irrelevant,
  "skip_reason": "If relevance_score < 6, briefly explain why. Otherwise empty string."
}}"""


def digest_paper(paper: dict) -> Optional[dict]:
    """Send paper to OpenRouter for digestion into social-ready content."""
    prompt = DIGEST_USER_PROMPT.format(
        title=paper["title"],
        source=paper["source"],
        year=paper.get("year", "n/d"),
        abstract=paper["abstract"],
    )

    try:
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
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=30,
        )
        raw = response.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        digest = json.loads(raw)
        digest["paper"] = {
            "title": paper["title"],
            "source": paper["source"],
            "url": paper["url"],
            "year": paper.get("year", ""),
            "citations": paper.get("citations", None),
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

    # DeSci section (injected if matches exist)
    desci_section = ""
    if d.get("desci"):
        desci_section = format_desci_section_telegram(d["desci"])

    # Add DeSci hashtags if matches found
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
        r = requests.post(url, json=payload, timeout=10)
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

    hashtags = " ".join(f"#{h}" for h in d.get("hashtags", []))
    desci_tags = "#DeSci #Web3Science" if d.get("desci", {}).get("matches") else ""

    # Base fields
    fields = [
        {"name": "💡 Actionable Tip", "value": d.get("actionable_tip", ""), "inline": False},
        {"name": "📊 Evidence Strength", "value": d.get("evidence_strength", ""), "inline": True},
        {"name": "🏷️ Hashtags", "value": f"{hashtags} {desci_tags}".strip(), "inline": False},
    ]

    # Add DeSci fields if matches exist
    if d.get("desci"):
        desci_fields = format_desci_section_discord(d["desci"])
        if desci_fields:
            fields.append({"name": "─" * 30, "value": "🔬 **DeSci Projects On This**", "inline": False})
            fields.extend(desci_fields)

    embed = {
        "title": f"{category_emoji} {d.get('category', 'Research')} | {p['source']} {p['year']}",
        "description": d.get("social_caption", ""),
        "color": 0x00C9A7,  # teal — DeSci vibes
        "fields": fields,
        "footer": {"text": p["title"][:100]},
        "url": p["url"],
    }
    payload = {"embeds": [embed]}
    try:
        r = requests.post(webhook_url, json=payload, timeout=10)
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

    seen_path = "seen_ids.json"
    output_path = f"digests_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    seen_ids = load_seen_ids(seen_path)
    all_papers = []

    # ── 1. Fetch from all sources ──
    print("📡 Fetching papers...\n")

    # PubMed (sample 3 queries to keep rate limit friendly)
    import random
    sampled_queries = random.sample(SEARCH_QUERIES, min(4, len(SEARCH_QUERIES)))
    for query in sampled_queries:
        print(f"  PubMed: {query}")
        papers = fetch_pubmed(query, max_results=2)
        all_papers.extend(papers)
        time.sleep(0.4)  # NCBI rate limit: 3 req/sec

    # Semantic Scholar (2 queries)
    for query in sampled_queries[:2]:
        print(f"  SemanticScholar: {query}")
        papers = fetch_semantic_scholar(query, max_results=2)
        all_papers.extend(papers)
        time.sleep(0.5)

    # ScienceDaily RSS
    print(f"  ScienceDaily RSS feeds...")
    sd_articles = fetch_sciencedaily()
    all_papers.extend(sd_articles)

    print(f"\n  → {len(all_papers)} total papers fetched")

    # ── 2. Deduplicate ──
    fresh_papers = []
    for p in all_papers:
        uid = hashlib.md5(f"{p['id']}{p['title']}".encode()).hexdigest()
        p["_uid"] = uid
        if uid not in seen_ids:
            fresh_papers.append(p)

    print(f"  → {len(fresh_papers)} new (after dedup)\n")

    if not fresh_papers:
        print("Nothing new today. Run again tomorrow!")
        return

    # ── 3. Digest with Claude ──
    print("🧠 Digesting with Claude...\n")
    good_digests = []
    min_score = CONFIG.get("min_relevance_score", 6)

    for paper in fresh_papers[:CONFIG.get("max_papers_per_run", 10)]:
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
            names = ", ".join(m["name"] for m in matches)
            print(f"     🔗 Matched: {names}")
        else:
            reason = desci_result.get("no_match_reason", "no close match")
            print(f"     ↳ No DeSci match ({reason})")

        time.sleep(0.3)
        good_digests.append(digest)
        seen_ids.add(paper["_uid"])

    print(f"\n  → {len(good_digests)} high-quality findings\n")

    # ── 4. Send notifications ──
    if good_digests:
        print("📬 Sending notifications...\n")
        for digest in good_digests:
            if CONFIG.get("telegram_bot_token") and CONFIG.get("telegram_chat_id"):
                send_telegram(
                    digest,
                    CONFIG["telegram_bot_token"],
                    CONFIG["telegram_chat_id"],
                )
                time.sleep(1)

            if CONFIG.get("discord_webhook_url"):
                send_discord(digest, CONFIG["discord_webhook_url"])
                time.sleep(1)

    # ── 5. Save output ──
    with open(output_path, "w") as f:
        json.dump(good_digests, f, indent=2)
    save_seen_ids(seen_ids, seen_path)

    print(f"\n✅ Done! Saved to {output_path}")
    print(f"   {len(good_digests)} findings sent\n")

    return good_digests


if __name__ == "__main__":
    run()
