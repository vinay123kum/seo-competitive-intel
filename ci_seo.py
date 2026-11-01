#!/usr/bin/env python3
"""
SEO / GEO / AEO Competitive Intelligence Tool
AI Visibility Gap Analysis
Usage: python ci_seo.py input.yaml
"""

import asyncio
import base64
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus, urlparse

# Auto-load .env from script directory
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        if "=" in _line and not _line.startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

try:
    import anthropic
except ImportError:
    anthropic = None  # optional — only needed if auto-discovering competitors/queries

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("ERROR: Run: pip install requests beautifulsoup4")
    sys.exit(1)

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

try:
    import yaml
except ImportError:
    print("ERROR: Run: pip install pyyaml")
    sys.exit(1)

SERVICE_URL = os.environ.get("REPORT_CTA_URL", "#")
AGENCY_NAME = os.environ.get("REPORT_AGENCY_NAME", "AI Visibility Report")

PLATFORM_CONFIGS = {
    "google": {
        "name": "Google AI Overview",
        "url": "https://www.google.com/search?q={query}",
        "wait_ms": 6000,
    },
    "perplexity": {
        "name": "Perplexity",
        "url": "https://www.perplexity.ai/search?q={query}",
        "wait_ms": 10000,
    },
    "bing": {
        "name": "Bing Copilot",
        "url": "https://www.bing.com/search?q={query}&showconv=1",
        "wait_ms": 7000,
    },
}

BADGE_JS = """
(params) => {
    const existing = document.getElementById('ci-badge');
    if (existing) existing.remove();
    const badge = document.createElement('div');
    badge.id = 'ci-badge';
    badge.style.cssText = [
        'position:fixed',
        'top:14px',
        'right:14px',
        'background:' + (params.cited ? '#16a34a' : '#dc2626'),
        'color:white',
        'padding:10px 18px',
        'border-radius:6px',
        'font-size:15px',
        'font-weight:700',
        'font-family:-apple-system,BlinkMacSystemFont,sans-serif',
        'z-index:2147483647',
        'box-shadow:0 4px 14px rgba(0,0,0,0.4)',
        'letter-spacing:0.3px',
        'line-height:1'
    ].join(';');
    badge.textContent = (params.cited ? '✓ ' : '✗ ') + params.domain + (params.cited ? ' CITED' : ' NOT CITED');
    document.body.appendChild(badge);
}
"""


# ─── Helpers ──────────────────────────────────────────────────────────────────

def slugify(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def image_to_b64(path):
    p = Path(path)
    if p.exists():
        return base64.b64encode(p.read_bytes()).decode()
    return ""


def score_color(score):
    if score >= 7:
        return "#16a34a"
    elif score >= 4:
        return "#d97706"
    return "#dc2626"


# ─── Website Fetching ─────────────────────────────────────────────────────────

def fetch_text(url, timeout=12):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text)[:6000]
    except Exception as e:
        return f"[fetch failed: {e}]"


def fetch_prospect_content(domain):
    pages = ["", "/pricing", "/features", "/product", "/about", "/solutions"]
    parts = []
    for page in pages[:4]:
        url = f"https://{domain}{page}"
        text = fetch_text(url)
        if not text.startswith("[fetch failed"):
            parts.append(f"=== {url} ===\n{text[:1800]}")
    return "\n\n".join(parts)


# ─── Claude Analysis ──────────────────────────────────────────────────────────

def analyze_prospect(client, name, domain, content):
    prompt = f"""You are an SEO competitive intelligence analyst specializing in B2B SaaS.

Analyze this company's website content and return ONLY a valid JSON object — no markdown, no explanation.

Company: {name}
Domain: {domain}

Website content:
{content}

Return this exact JSON structure:
{{
  "category": "short category label (e.g. 'Employee Engagement Software')",
  "icp": "ideal customer profile (e.g. 'HR leaders at 200-2000 person B2B SaaS companies')",
  "value_props": ["prop1", "prop2", "prop3"],
  "positioning_summary": "2-3 sentence summary of how this company positions itself",
  "target_queries": [
    "query 1 — informational/comparison query a B2B buyer would actually search",
    "query 2",
    "query 3",
    "query 4",
    "query 5",
    "query 6"
  ],
  "competitors": [
    {{"name": "CompetitorName", "domain": "competitor.com"}},
    {{"name": "CompetitorName2", "domain": "competitor2.com"}},
    {{"name": "CompetitorName3", "domain": "competitor3.com"}},
    {{"name": "CompetitorName4", "domain": "competitor4.com"}}
  ]
}}

Rules for target_queries:
- Use real buyer language, not marketing speak
- Include: "best X for Y", "top X tools", "how to Z", "X alternatives", "X vs Y" type queries
- Must be queries where appearing in Google AI Overview / Perplexity / Bing Copilot would matter
- Do NOT use branded queries (prospect's own name)

Rules for competitors:
- Must be real, direct competitors in the same category
- Use their actual domain (e.g. lattice.com not lattice-hq.com)
- Pick the 4 most prominent ones a prospect buyer would compare"""

    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    # Strip code fences if present
    if "```" in text:
        m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
        if m:
            text = m.group(1)
    return json.loads(text)


def generate_gap_narrative(client, prospect_name, results, queries, scores, competitors):
    prospect_score = scores["prospect"]["score"]
    best_comp_name = max(competitors, key=lambda c: scores[c["domain"]]["score"])["name"]
    best_comp_score = scores[max(competitors, key=lambda c: scores[c["domain"]]["score"])["domain"]]["score"]

    lines = []
    for query in queries:
        cited_by = []
        not_cited = []
        for company_key, q_data in results.items():
            platforms_cited = [p for p, d in q_data.get(query, {}).items() if d.get("cited")]
            if platforms_cited:
                cited_by.append(f"{company_key} ({', '.join(platforms_cited)})")
            else:
                not_cited.append(company_key)
        lines.append(f'"{query}": cited → {", ".join(cited_by) or "nobody"}; not cited → {", ".join(not_cited)}')

    prompt = f"""Write a gap analysis section for a competitive intelligence report prepared by {AGENCY_NAME} (an ASEO agency) for their prospect: {prospect_name}.

AI Visibility Scores (out of 10):
- {prospect_name}: {prospect_score}/10
- Best competitor ({best_comp_name}): {best_comp_score}/10
- Gap: {round(best_comp_score - prospect_score, 1)} points

Per-query citation breakdown:
{chr(10).join(lines)}

Write 3-4 punchy paragraphs that:
1. Lead with the hard number (score gap) as the headline problem
2. Show specifically which queries the prospect is losing and to whom
3. Explain the pipeline impact — what buyers are finding instead
4. End with a bridge to why ASEO/GEO/AEO investment closes this gap

Tone: direct, data-driven, written for a CMO or VP Marketing. No fluff, no bullet points — prose only."""

    if client is None:
        return _template_gap_narrative(prospect_name, prospect_score, best_comp_name, best_comp_score, gap, lines)
    response = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()


def _template_gap_narrative(prospect_name, prospect_score, best_comp_name, best_comp_score, gap, lines):
    cited_queries = [l.split(":")[0].strip('"') for l in lines if "cited → nobody" not in l and "cited →" in l and prospect_name.lower() not in l.split("cited →")[0].lower()]
    missing = [l.split(":")[0].strip('"') for l in lines]

    return f"""{prospect_name} currently scores {prospect_score}/10 on AI Visibility — meaning that across the queries B2B buyers use to find services like yours, you are cited in only a fraction of available AI answer slots. {best_comp_name} leads the competitive set at {best_comp_score}/10, a gap of {gap} points that directly translates to mindshare lost at the top of the modern buyer journey.

This is not a traditional SEO gap — it is an AI citation gap. When a buyer asks Google, Perplexity, or Bing Copilot about topics where {prospect_name} should be the obvious answer, the AI engines are pulling from sources that mention your competitors instead. Every query where {prospect_name} is not cited is a buyer conversation that starts with someone else's name in the answer.

The pipeline impact is structural: AI-generated answers now appear above organic results for the majority of informational and comparison queries. Buyers who receive an AI Overview or Perplexity answer citing {best_comp_name} are forming vendor shortlists before they ever visit a search results page. {prospect_name} is invisible in that moment — and invisible means not considered.

ASEO, GEO, and AEO close this gap by making {prospect_name} the cited source AI engines trust. This requires earning high-authority citations, structuring content for AI answer extraction, and building topical authority signals across the exact queries in this report. 90-day AI visibility programs are designed to move scores like this from {prospect_score} toward double digits — measurably and repeatably."""


# ─── Screenshot + Citation ────────────────────────────────────────────────────

def is_domain_cited(body_text, domain):
    domain_clean = domain.lower().replace("www.", "")
    return domain_clean in body_text.lower()


async def screenshot_platform(browser, platform_key, query, company_domain, output_path, wait_ms_override=None):
    cfg = PLATFORM_CONFIGS[platform_key]
    url = cfg["url"].format(query=quote_plus(query))
    wait_ms = wait_ms_override or cfg["wait_ms"]

    page = await browser.new_page(viewport={"width": 1440, "height": 900})
    cited = False

    try:
        await page.goto(url, wait_until="networkidle", timeout=35000)
    except Exception:
        try:
            await page.goto(url, timeout=35000)
        except Exception:
            pass

    await page.wait_for_timeout(wait_ms)

    # Citation detection
    try:
        body_text = await page.evaluate("() => document.body.innerText || ''")
        cited = is_domain_cited(body_text, company_domain)

        # Second attempt for Perplexity — wait longer if no answer yet
        if platform_key == "perplexity" and len(body_text) < 500:
            await page.wait_for_timeout(5000)
            body_text = await page.evaluate("() => document.body.innerText || ''")
            cited = is_domain_cited(body_text, company_domain)
    except Exception:
        cited = False

    # Inject CITED / NOT CITED badge
    try:
        display_domain = company_domain.replace("www.", "")
        await page.evaluate(BADGE_JS, {"cited": cited, "domain": display_domain})
        await page.wait_for_timeout(400)
    except Exception:
        pass

    # Screenshot (viewport only — faster, more readable)
    try:
        await page.screenshot(path=str(output_path), full_page=False)
    except Exception as e:
        print(f"      screenshot error: {e}")

    await page.close()
    return cited


async def run_all_screenshots(prospect, competitors, queries, output_dir, wait_overrides=None):
    """
    Returns nested dict:
    results["prospect"][query][platform] = {"cited": bool, "screenshot": path}
    results[competitor_domain][query][platform] = {...}
    """
    all_companies = [{"key": "prospect", "name": prospect["name"], "domain": prospect["domain"]}]
    for c in competitors:
        all_companies.append({"key": c["domain"], "name": c["name"], "domain": c["domain"]})

    results = {c["key"]: {} for c in all_companies}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for query in queries:
            q_slug = slugify(query)[:48]
            print(f"\n  Query: \"{query}\"")

            for company in all_companies:
                results[company["key"]][query] = {}

                for platform_key in ["google", "perplexity", "bing"]:
                    fname = f"{slugify(company['name'])}_{q_slug}_{platform_key}.png"
                    out_path = output_dir / fname
                    wait_ms = (wait_overrides or {}).get(platform_key)

                    print(f"    [{platform_key:10s}] {company['name'][:25]:<25} ... ", end="", flush=True)

                    try:
                        cited = await screenshot_platform(
                            browser, platform_key, query,
                            company["domain"], out_path, wait_ms
                        )
                        results[company["key"]][query][platform_key] = {
                            "cited": cited,
                            "screenshot": str(out_path),
                        }
                        print("CITED" if cited else "not cited")
                    except Exception as e:
                        print(f"ERROR: {e}")
                        results[company["key"]][query][platform_key] = {
                            "cited": False,
                            "screenshot": None,
                        }

        await browser.close()

    return results


# ─── Scoring ──────────────────────────────────────────────────────────────────

def calculate_scores(results, queries, competitors):
    """
    Returns scores dict keyed by company_key.
    Score = (total citations / possible citations) * 10
    """
    scores = {}
    total_possible = len(queries) * 3  # 3 platforms

    all_keys = ["prospect"] + [c["domain"] for c in competitors]

    for key in all_keys:
        per_platform = {"google": 0, "perplexity": 0, "bing": 0}
        per_query = {}
        total_cited = 0

        for query in queries:
            q_data = results.get(key, {}).get(query, {})
            q_count = 0
            for platform, data in q_data.items():
                if data.get("cited"):
                    total_cited += 1
                    q_count += 1
                    per_platform[platform] += 1
            per_query[query] = q_count

        score = round((total_cited / total_possible) * 10, 1) if total_possible > 0 else 0.0
        scores[key] = {
            "score": score,
            "total_cited": total_cited,
            "total_possible": total_possible,
            "per_platform": per_platform,
            "per_query": per_query,
        }

    return scores


# ─── HTML Report ──────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f1f5f9; color: #0f172a; line-height: 1.5; }

/* ── Cover ── */
.cover { background: linear-gradient(135deg, #0f0c29 0%, #302b63 55%, #24243e 100%); color: white; padding: 72px 64px 80px; }
.cover-badge { font-size: 12px; letter-spacing: 3px; text-transform: uppercase; color: #a78bfa; margin-bottom: 20px; font-weight: 600; }
.cover h1 { font-size: 48px; font-weight: 900; margin-bottom: 10px; line-height: 1.1; }
.cover-sub { font-size: 20px; color: rgba(255,255,255,0.7); margin-bottom: 6px; }
.cover-category { font-size: 14px; color: #a78bfa; margin-top: 14px; }
.cover-meta { margin-top: 32px; font-size: 13px; color: rgba(255,255,255,0.4); border-top: 1px solid rgba(255,255,255,0.1); padding-top: 20px; }

/* ── Sections ── */
.section { padding: 52px 64px; }
.section-white { background: white; }
.section-gray { background: #f8fafc; }
.section h2 { font-size: 26px; font-weight: 800; margin-bottom: 8px; color: #1e1b4b; }
.section-desc { font-size: 15px; color: #64748b; margin-bottom: 36px; }

/* ── Exec Summary Stats ── */
.stats-row { display: flex; gap: 20px; margin-bottom: 40px; }
.stat-card { flex: 1; border-radius: 14px; padding: 32px 24px; text-align: center; }
.stat-prospect { background: linear-gradient(135deg, #1e1b4b, #4338ca); color: white; }
.stat-competitor { background: #f0fdf4; border: 2px solid #86efac; }
.stat-gap { background: #fff7ed; border: 2px solid #fed7aa; }
.stat-score { font-size: 58px; font-weight: 900; line-height: 1; }
.stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; margin-top: 8px; opacity: 0.7; }
.stat-name { font-size: 14px; font-weight: 600; margin-top: 6px; }

.findings { background: #f8fafc; border-radius: 12px; padding: 28px 32px; border-left: 4px solid #4f46e5; }
.findings h3 { font-size: 14px; text-transform: uppercase; letter-spacing: 1px; color: #4f46e5; margin-bottom: 16px; }
.findings ul { list-style: none; }
.findings li { padding: 9px 0; border-bottom: 1px solid #e2e8f0; font-size: 15px; color: #374151; }
.findings li:last-child { border-bottom: none; }
.findings li::before { content: "→ "; color: #4f46e5; font-weight: 700; }

/* ── Query blocks ── */
.query-block { margin-bottom: 48px; border-radius: 10px; overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.query-label { background: linear-gradient(90deg, #4f46e5, #7c3aed); color: white; padding: 14px 22px; font-size: 15px; font-weight: 600; }
.query-label span { opacity: 0.6; font-size: 12px; margin-right: 8px; text-transform: uppercase; letter-spacing: 1px; }
.platforms-grid { display: grid; grid-template-columns: repeat(3, 1fr); background: white; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 10px 10px; overflow: hidden; }
.platform-col { border-right: 1px solid #e2e8f0; }
.platform-col:last-child { border-right: none; }
.platform-header { padding: 12px 16px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #64748b; }

.company-row { border-bottom: 1px solid #f1f5f9; }
.company-row:last-child { border-bottom: none; }
.company-row.is-prospect { border-left: 3px solid #4f46e5; }
.company-meta { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; background: #fafafa; }
.company-meta.is-prospect { background: #eef2ff; }
.company-name { font-size: 12px; font-weight: 600; color: #374151; }
.company-name .star { color: #4f46e5; }
.badge { font-size: 10px; font-weight: 700; padding: 3px 7px; border-radius: 4px; letter-spacing: 0.5px; white-space: nowrap; }
.badge-cited { background: #dcfce7; color: #15803d; }
.badge-not { background: #fee2e2; color: #b91c1c; }
.screenshot-wrap { position: relative; }
.screenshot-wrap img { width: 100%; height: 180px; object-fit: cover; object-position: top; display: block; }
.no-shot { height: 180px; background: #f9fafb; display: flex; align-items: center; justify-content: center; color: #9ca3af; font-size: 13px; }

/* ── Comparison Table ── */
.comp-table { width: 100%; border-collapse: collapse; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
.comp-table th { background: #1e1b4b; color: white; padding: 14px 18px; text-align: left; font-size: 12px; letter-spacing: 0.5px; text-transform: uppercase; }
.comp-table td { padding: 14px 18px; border-bottom: 1px solid #f1f5f9; font-size: 14px; }
.comp-table tr:last-child td { border-bottom: none; }
.comp-table tr:nth-child(even) td { background: #f8fafc; }
.comp-table tr.prospect-row td { background: #eef2ff !important; font-weight: 600; }
.score-pill { display: inline-block; padding: 5px 14px; border-radius: 20px; font-weight: 800; font-size: 15px; color: white; }

/* ── Gap Section ── */
.gap-section { background: linear-gradient(135deg, #0f0c29 0%, #1e1b4b 100%); color: white; padding: 64px; }
.gap-section h2 { color: white; font-size: 28px; margin-bottom: 28px; }
.gap-narrative p { font-size: 16px; line-height: 1.85; color: rgba(255,255,255,0.82); margin-bottom: 18px; }
.cta-box { margin-top: 40px; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.15); border-radius: 14px; padding: 32px; }
.cta-box h3 { color: #a78bfa; font-size: 18px; margin-bottom: 10px; }
.cta-box p { color: rgba(255,255,255,0.75); font-size: 15px; margin-bottom: 22px; }
.cta-btn { display: inline-block; background: #4f46e5; color: white; padding: 14px 30px; border-radius: 8px; text-decoration: none; font-weight: 700; font-size: 15px; letter-spacing: 0.3px; }
.cta-btn:hover { background: #4338ca; }

/* ── Platform score chips ── */
.platform-chips { display: flex; gap: 12px; margin-top: 20px; }
.platform-chip { flex: 1; background: #f1f5f9; border-radius: 10px; padding: 14px; text-align: center; }
.chip-count { font-size: 26px; font-weight: 800; color: #4f46e5; }
.chip-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
"""


def build_query_html(query, all_companies, results, prospect_domain):
    platform_cols = ""
    for platform_key, platform_name in [("google", "Google AI Overview"), ("perplexity", "Perplexity"), ("bing", "Bing Copilot")]:
        rows = ""
        for company in all_companies:
            key = "prospect" if company["domain"] == prospect_domain else company["domain"]
            is_prospect = company["domain"] == prospect_domain
            data = results.get(key, {}).get(query, {}).get(platform_key, {})
            cited = data.get("cited", False)
            shot_path = data.get("screenshot")
            b64 = image_to_b64(shot_path) if shot_path else ""

            if b64:
                img_html = f'<img src="data:image/png;base64,{b64}" alt="{company["name"]} {platform_name}" loading="lazy" />'
            else:
                img_html = '<div class="no-shot">Screenshot unavailable</div>'

            badge_cls = "badge-cited" if cited else "badge-not"
            badge_txt = "CITED" if cited else "NOT CITED"
            row_cls = "company-row is-prospect" if is_prospect else "company-row"
            meta_cls = "company-meta is-prospect" if is_prospect else "company-meta"
            star = '<span class="star">★ </span>' if is_prospect else ""

            rows += f"""<div class="{row_cls}">
  <div class="{meta_cls}">
    <span class="company-name">{star}{company['name']}</span>
    <span class="badge {badge_cls}">{badge_txt}</span>
  </div>
  <div class="screenshot-wrap">{img_html}</div>
</div>"""

        platform_cols += f"""<div class="platform-col">
  <div class="platform-header">{platform_name}</div>
  {rows}
</div>"""

    q_slug = slugify(query)
    return f"""<div class="query-block">
  <div class="query-label"><span>Query</span>"{query}"</div>
  <div class="platforms-grid">{platform_cols}</div>
</div>"""


def build_table_rows(all_companies, scores, prospect_domain, queries):
    rows = ""
    for company in all_companies:
        key = "prospect" if company["domain"] == prospect_domain else company["domain"]
        s = scores[key]
        score = s["score"]
        color = score_color(score)
        pp = s["per_platform"]
        is_prospect = company["domain"] == prospect_domain
        row_cls = ' class="prospect-row"' if is_prospect else ""
        star = "★ " if is_prospect else ""
        rows += f"""<tr{row_cls}>
  <td>{star}{company['name']}</td>
  <td><span class="score-pill" style="background:{color}">{score}/10</span></td>
  <td>{s['total_cited']}/{s['total_possible']}</td>
  <td>{pp['google']}/{len(queries)}</td>
  <td>{pp['perplexity']}/{len(queries)}</td>
  <td>{pp['bing']}/{len(queries)}</td>
</tr>"""
    return rows


def generate_html(prospect, competitors, queries, results, scores, analysis, gap_narrative, output_dir):
    all_companies = [{"name": prospect["name"], "domain": prospect["domain"]}] + competitors
    prospect_domain = prospect["domain"]
    p_score = scores["prospect"]["score"]
    p_data = scores["prospect"]

    best_comp = max(competitors, key=lambda c: scores[c["domain"]]["score"])
    best_comp_score = scores[best_comp["domain"]]["score"]
    gap = round(best_comp_score - p_score, 1)

    # Key findings
    findings = [
        f"{prospect['name']} appears in {p_data['total_cited']} of {p_data['total_possible']} possible AI answer slots ({p_score}/10)",
        f"{best_comp['name']} leads all competitors at {best_comp_score}/10 — a {gap}-point gap",
        f"Google AI Overview: {p_data['per_platform']['google']}/{len(queries)} queries cited",
        f"Perplexity: {p_data['per_platform']['perplexity']}/{len(queries)} queries cited",
        f"Bing Copilot: {p_data['per_platform']['bing']}/{len(queries)} queries cited",
        f"Competitors collectively cited {sum(scores[c['domain']]['total_cited'] for c in competitors)} times vs {prospect['name']}'s {p_data['total_cited']}",
    ]
    findings_html = "".join(f"<li>{f}</li>" for f in findings)

    queries_html = "".join(build_query_html(q, all_companies, results, prospect_domain) for q in queries)
    table_rows = build_table_rows(all_companies, scores, prospect_domain, queries)
    gap_html = "".join(f"<p>{para}</p>" for para in gap_narrative.split("\n\n") if para.strip())

    # Platform chips for exec summary
    chips = ""
    for pk, pname in [("google", "Google AI Overview"), ("perplexity", "Perplexity"), ("bing", "Bing Copilot")]:
        count = p_data["per_platform"][pk]
        chips += f"""<div class="platform-chip">
  <div class="chip-count">{count}/{len(queries)}</div>
  <div class="chip-label">{pname}</div>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Visibility Report: {prospect['name']} | {AGENCY_NAME}</title>
<style>{CSS}</style>
</head>
<body>

<!-- COVER -->
<div class="cover">
  <div class="cover-badge">{AGENCY_NAME} · AI SEO Intelligence</div>
  <h1>AI Visibility Gap Report</h1>
  <div class="cover-sub">{prospect['name']}</div>
  <div class="cover-category">{analysis.get('category', 'B2B SaaS')} · ASEO · GEO · AEO Analysis</div>
  <div class="cover-meta">Prepared {datetime.now().strftime('%B %d, %Y')} · Confidential · {len(queries)} queries · {len(all_companies)} companies · 3 AI platforms</div>
</div>

<!-- EXEC SUMMARY -->
<div class="section section-white">
  <h2>Executive Summary</h2>
  <p class="section-desc">AI Visibility Scores measure how often each company is cited in Google AI Overview, Perplexity, and Bing Copilot across {len(queries)} buyer queries. Score is out of 10.</p>
  <div class="stats-row">
    <div class="stat-card stat-prospect">
      <div class="stat-score">{p_score}</div>
      <div class="stat-label">AI Visibility Score</div>
      <div class="stat-name">{prospect['name']}</div>
    </div>
    <div class="stat-card stat-competitor">
      <div class="stat-score" style="color:#15803d">{best_comp_score}</div>
      <div class="stat-label">Best Competitor Score</div>
      <div class="stat-name">{best_comp['name']}</div>
    </div>
    <div class="stat-card stat-gap">
      <div class="stat-score" style="color:#c2410c">{gap}</div>
      <div class="stat-label">Visibility Gap</div>
      <div class="stat-name">Points Behind Leader</div>
    </div>
  </div>
  <div class="platform-chips">{chips}</div>
  <div class="findings" style="margin-top:28px">
    <h3>Key Findings</h3>
    <ul>{findings_html}</ul>
  </div>
</div>

<!-- PER QUERY BREAKDOWN -->
<div class="section section-gray">
  <h2>Query-by-Query AI Visibility</h2>
  <p class="section-desc">
    For each buyer query, we show who gets cited in Google AI Overview, Perplexity, and Bing Copilot.
    <strong>★ marks {prospect['name']}.</strong> Badges show CITED (appeared in AI answer sources) or NOT CITED.
  </p>
  {queries_html}
</div>

<!-- COMPARISON TABLE -->
<div class="section section-white">
  <h2>Competitive Comparison</h2>
  <p class="section-desc">Full AI Visibility Scores across all companies and platforms.</p>
  <table class="comp-table">
    <thead>
      <tr>
        <th>Company</th>
        <th>AI Visibility Score</th>
        <th>Total Citations</th>
        <th>Google AI Overview</th>
        <th>Perplexity</th>
        <th>Bing Copilot</th>
      </tr>
    </thead>
    <tbody>{table_rows}</tbody>
  </table>
</div>

<!-- GAP ANALYSIS -->
<div class="gap-section">
  <h2>The Visibility Gap — And How to Close It</h2>
  <div class="gap-narrative">{gap_html}</div>
  <div class="cta-box">
    <h3>What Closes the Gap</h3>
    <p>We specialize in ASEO, GEO, and AEO — the exact disciplines that get B2B SaaS companies cited in AI-generated answers. Clients see measurable AI visibility gains within 90 days.</p>
    <a class="cta-btn" href="{SERVICE_URL}" target="_blank">See How We Do It →</a>
  </div>
</div>

</body>
</html>"""

    out = output_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    if len(sys.argv) < 2:
        print("Usage: python ci_seo.py input.yaml")
        print("\nMinimal input.yaml:")
        print("  prospect_name: Acme")
        print("  prospect_url: https://acme.com")
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        inp = yaml.safe_load(f)

    prospect_name = inp["prospect_name"]
    prospect_url = inp["prospect_url"].rstrip("/")
    prospect_domain = urlparse(prospect_url).netloc.replace("www.", "")
    wait_overrides = inp.get("wait_ms", {})

    print(f"\n{'='*62}")
    print(f"  SEO-GEO-AEO CI Report")
    print(f"  Prospect : {prospect_name} ({prospect_domain})")
    print(f"{'='*62}")

    # Output directory
    out_slug = slugify(prospect_name)
    output_dir = Path("outputs") / f"CI_{out_slug}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n  Output   : {output_dir.resolve()}")

    # Anthropic client (optional — only needed when auto-discovering competitors/queries)
    needs_claude = "competitors" not in inp or "queries" not in inp
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if needs_claude and api_key and anthropic:
        client = anthropic.Anthropic(api_key=api_key)
    elif needs_claude:
        print("\nERROR: competitors and queries not in YAML, and no valid ANTHROPIC_API_KEY set.")
        print("Add 'competitors' and 'queries' to input.yaml, or set ANTHROPIC_API_KEY.")
        sys.exit(1)
    else:
        client = None

    # ── Step 1: Analyze prospect ──────────────────────────────────────────────
    print("\n[1/4] Analyzing prospect website...")
    if client is not None:
        content = fetch_prospect_content(prospect_domain)
        analysis = analyze_prospect(client, prospect_name, prospect_domain, content)
    else:
        analysis = {"category": inp.get("category", "B2B SaaS"), "icp": inp.get("icp", "")}

    # Allow YAML overrides
    if "competitors" in inp:
        competitors = inp["competitors"]
        print(f"       Competitors : {[c['name'] for c in competitors]} (from YAML)")
    else:
        competitors = analysis["competitors"]
        print(f"       Competitors : {[c['name'] for c in competitors]} (auto-discovered)")

    if "queries" in inp:
        queries = inp["queries"]
        print(f"       Queries     : {len(queries)} (from YAML)")
    else:
        queries = analysis["target_queries"]
        print(f"       Queries     : {len(queries)} (auto-generated)")

    print(f"       Category    : {analysis.get('category')}")
    print(f"       ICP         : {analysis.get('icp', '')}")

    prospect = {"name": prospect_name, "domain": prospect_domain}

    # ── Step 2: Screenshots ───────────────────────────────────────────────────
    total_shots = len(queries) * (len(competitors) + 1) * 3
    print(f"\n[2/4] Capturing {total_shots} screenshots...")
    results = await run_all_screenshots(prospect, competitors, queries, output_dir, wait_overrides)

    # ── Step 3: Scoring ───────────────────────────────────────────────────────
    print("\n[3/4] Calculating AI Visibility Scores...")
    scores = calculate_scores(results, queries, competitors)

    all_companies = [prospect] + competitors
    for company in all_companies:
        key = "prospect" if company["domain"] == prospect_domain else company["domain"]
        s = scores[key]
        bar = "█" * int(s["score"]) + "░" * (10 - int(s["score"]))
        print(f"       {company['name']:<30} {s['score']:4.1f}/10  [{bar}]")

    # ── Step 4: Gap narrative + report ────────────────────────────────────────
    print("\n[4/4] Writing gap analysis and generating report...")
    gap = generate_gap_narrative(client, prospect_name, results, queries, scores, competitors)

    report_path = generate_html(
        prospect, competitors, queries, results, scores, analysis, gap, output_dir
    )

    prospect_score = scores["prospect"]["score"]
    best_comp = max(competitors, key=lambda c: scores[c["domain"]]["score"])
    best_score = scores[best_comp["domain"]]["score"]

    print(f"\n{'='*62}")
    print(f"  ✓ Report saved: {report_path.resolve()}")
    print(f"  AI Visibility Score : {prospect_name} {prospect_score}/10 vs {best_comp['name']} {best_score}/10")
    print(f"  Gap                 : {round(best_score - prospect_score, 1)} points")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    asyncio.run(main())
