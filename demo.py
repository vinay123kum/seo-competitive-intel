"""Render a sample report with no API keys or browsing. All companies and results are fictional."""
import random
from pathlib import Path
import ci_seo

prospect = {"name": "Northwind Analytics", "domain": "northwind-analytics.example"}
comps = [{"name": n, "domain": n.lower().replace(" ", "-") + ".example"}
         for n in ["Contoso Insights", "Fabrikam Data", "Tailspin Metrics", "Lumen BI"]]
queries = ["best product analytics tool", "top b2b analytics platforms", "product analytics for saas",
           "self-serve analytics software", "alternatives to legacy bi tools"]
random.seed(7)
rate = {"prospect": .15, comps[0]["domain"]: .75, comps[1]["domain"]: .55, comps[2]["domain"]: .4, comps[3]["domain"]: .3}
results = {k: {q: {p: {"cited": random.random() < r} for p in ("google", "perplexity", "bing")} for q in queries}
           for k, r in rate.items()}
scores = ci_seo.calculate_scores(results, queries, comps)
best = max(comps, key=lambda c: scores[c["domain"]]["score"])
narrative = (f"{prospect['name']} scores {scores['prospect']['score']}/10 while {best['name']} scores "
             f"{scores[best['domain']]['score']}/10. Buyers asking AI tools about this category mostly hear about competitors.\n\n"
             "The gap comes from third-party citations and listicle coverage, and both can be closed with focused content.")
out = Path("outputs/sample")
out.mkdir(parents=True, exist_ok=True)
f = ci_seo.generate_html(prospect, comps, queries, results, scores, {"category": "Product Analytics"}, narrative, out)
Path("sample_report.html").write_bytes(f.read_bytes())
print("Wrote sample_report.html")
