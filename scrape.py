"""Brand & content scraper for website redesigns.

Usage:
    python scrape.py https://example.com [--pages 8] [--max-images 150] [--out output]

Produces output/<domain>/ containing:
    brand-report.md   - human/Claude-readable brand + business brief
    data.json         - all raw extracted data
    palette.png       - colour swatches
    logo/             - logo candidates (original files + rendered PNGs)
    images/           - downloaded site imagery (+ images.csv manifest)
    screenshots/      - desktop & mobile screenshots
    pages/            - full text content of each crawled page
"""

import argparse
import colorsys
import csv
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urldefrag, urlparse

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
EXTRACT_JS = (HERE / "extract.js").read_text(encoding="utf-8")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

PRIORITY_WORDS = ["about", "contact", "service", "product", "menu", "team", "gallery", "portfolio",
                  "pricing", "price", "location", "our-story", "work", "shop", "faq", "testimonial",
                  "review", "project", "book", "why", "offer"]
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|webp|svg|zip|docx?|xlsx?|mp4|mp3|ics|xml)$", re.I)
SOCIAL = {"facebook.com": "Facebook", "instagram.com": "Instagram", "linkedin.com": "LinkedIn",
          "twitter.com": "X / Twitter", "x.com": "X / Twitter", "youtube.com": "YouTube",
          "tiktok.com": "TikTok", "pinterest.": "Pinterest", "yelp.": "Yelp",
          "tripadvisor.": "TripAdvisor", "g.page": "Google Business", "maps.app.goo.gl": "Google Maps",
          "google.com/maps": "Google Maps", "wa.me": "WhatsApp", "threads.net": "Threads"}
PLATFORMS = {"wp-content": "WordPress", "wix.com": "Wix", "wixstatic": "Wix", "squarespace": "Squarespace",
             "shopify": "Shopify", "webflow": "Webflow", "godaddy": "GoDaddy Website Builder",
             "weebly": "Weebly", "duda": "Duda", "framer": "Framer", "elementor": "Elementor (WordPress)"}
BUSINESS_KEYWORDS = {
    "Restaurant / Café": ["menu", "restaurant", "cafe", "café", "dining", "coffee", "brunch", "takeaway", "bistro", "bar"],
    "Trades / Home services": ["plumb", "electric", "roofing", "builder", "renovat", "hvac", "air condition", "landscap", "carpent", "painting", "handyman"],
    "Health / Medical": ["clinic", "dental", "dentist", "physio", "doctor", "medical", "chiro", "patient", "health"],
    "Beauty / Wellness": ["salon", "beauty", "hair", "spa", "massage", "nail", "barber", "lash", "skin"],
    "Fitness": ["gym", "fitness", "personal train", "yoga", "pilates", "crossfit", "workout"],
    "Legal": ["lawyer", "solicitor", "legal", "attorney", "law firm", "conveyanc"],
    "Accounting / Finance": ["accountant", "accounting", "tax", "bookkeep", "financial plan", "mortgage", "broker"],
    "Real estate": ["real estate", "property", "properties", "realtor", "rental", "for sale", "agent"],
    "Retail / E-commerce": ["shop", "add to cart", "store", "buy now", "checkout", "products"],
    "Hospitality / Accommodation": ["hotel", "accommodation", "rooms", "motel", "b&b", "stay", "booking"],
    "Automotive": ["mechanic", "car service", "auto", "tyre", "tire", "vehicle", "panel beat"],
    "Education / Childcare": ["school", "tutor", "course", "childcare", "kindergarten", "learning", "classes"],
    "Creative / Agency": ["design", "agency", "marketing", "branding", "photography", "video", "studio"],
    "Technology / IT": ["software", "it support", "cloud", "saas", "app", "tech", "cyber"],
    "Events / Weddings": ["wedding", "event", "venue", "catering", "celebrat"],
    "Pets / Veterinary": ["vet", "veterinary", "pet", "dog", "grooming", "animal"],
    "Non-profit / Community": ["charity", "donate", "volunteer", "non-profit", "nonprofit", "community"],
}
DAYS = r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*"


# --------------------------------------------------------------------------- helpers

def log(msg):
    print(msg, flush=True)


def slug(text, n=50):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return s[:n] or "item"


def same_site(url, base_host):
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host == base_host


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(x) for x in rgb[:3])


def color_dist(a, b):
    (r1, g1, b1), (r2, g2, b2) = hex_to_rgb(a), hex_to_rgb(b)
    rm = (r1 + r2) / 2  # "redmean" perceptual approximation
    return (((512 + rm) * (r1 - r2) ** 2) / 256 + 4 * (g1 - g2) ** 2 + ((767 - rm) * (b1 - b2) ** 2) / 256) ** 0.5


def saturation(h):
    r, g, b = (x / 255 for x in hex_to_rgb(h))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return ss if 0.06 < ll < 0.94 else 0


def lightness(h):
    r, g, b = (x / 255 for x in hex_to_rgb(h))
    return colorsys.rgb_to_hls(r, g, b)[1]


def color_name(h):
    """Rough human description, e.g. 'dark blue'."""
    r, g, b = (x / 255 for x in hex_to_rgb(h))
    hue, l, s = colorsys.rgb_to_hls(r, g, b)
    if l > 0.94:
        return "white"
    if l < 0.08:
        return "black"
    if s < 0.12:
        return "light grey" if l > 0.7 else "dark grey" if l < 0.3 else "grey"
    names = [(15, "red"), (40, "orange"), (65, "yellow"), (160, "green"), (195, "teal"),
             (250, "blue"), (290, "purple"), (335, "pink"), (360, "red")]
    deg = hue * 360
    base = next(n for lim, n in names if deg <= lim)
    if base == "orange" and l < 0.35:
        base = "brown"
    tone = "dark " if l < 0.3 else "light " if l > 0.72 else ""
    return tone + base


def contrast(a, b):
    def lum(h):
        c = [x / 255 for x in hex_to_rgb(h)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    l1, l2 = sorted([lum(a), lum(b)], reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def merge_colors(weights, threshold=28):
    """Cluster near-identical colours, keeping the heaviest as representative."""
    clusters = []
    for c, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        for cl in clusters:
            if color_dist(cl["hex"], c) < threshold:
                cl["weight"] += w
                cl["variants"].append(c)
                break
        else:
            clusters.append({"hex": c, "weight": w, "variants": [c]})
    return clusters


def normalise(d):
    total = sum(d.values()) or 1
    return {k: v / total for k, v in d.items()}


def first_font(stack):
    return (stack or "").split(",")[0].strip().strip("'\"")


# --------------------------------------------------------------------------- crawling

def load_page(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=12000)
    except Exception:
        pass
    # Scroll through to trigger lazy-loaded images, then return to top.
    page.evaluate("""async () => {
        const step = innerHeight * 0.8;
        for (let i = 0; i < 40 && scrollY + innerHeight < document.body.scrollHeight; i++) {
            scrollBy(0, step); await new Promise(r => setTimeout(r, 250));
        }
        scrollTo(0, 0); await new Promise(r => setTimeout(r, 600));
    }""")
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    hide_overlays(page)


def hide_overlays(page):
    """Remove cookie/consent banners so they don't pollute styles or screenshots (nothing is accepted)."""
    page.evaluate("""() => {
        const vendors = '#onetrust-consent-sdk, #CybotCookiebotDialog, .cc-window, #cookie-law-info-bar, ' +
            '#cmplz-cookiebanner-container, .cky-consent-container, #usercentrics-root, .termly-styles-root, #iubenda-cs-banner';
        document.querySelectorAll(vendors).forEach(e => e.remove());
        for (const el of document.querySelectorAll('body *')) {
            if (!el.isConnected) continue;
            const s = getComputedStyle(el);
            if (s.position === 'fixed' || s.position === 'sticky') {
                const t = (el.innerText || '').slice(0, 3000);
                if (/cookie|consent|gdpr|we value your privacy/i.test(t) && !el.querySelector('nav')) el.remove();
            }
        }
        for (const e of [document.documentElement, document.body]) if (e) e.style.setProperty('overflow', 'auto', 'important');
    }""")


def pick_links(data, base_host, seen, limit):
    scored = {}
    for link in data["links"]:
        href = link.get("href") or ""
        if not href.startswith("http"):
            continue
        href = urldefrag(href)[0].rstrip("/")
        if not same_site(href, base_host) or SKIP_EXT.search(href) or href in seen:
            continue
        if re.search(r"/(wp-admin|wp-login|cart|checkout|account|login|register|feed|tag|author)\b|\?", href):
            continue
        hay = (href + " " + link.get("text", "")).lower()
        score = sum(3 for w in PRIORITY_WORDS if w in hay)
        if re.search(r"terms|privacy|policy|cookie|legal|disclaimer|sitemap|accessibility", href, re.I):
            score -= 20  # only crawled if nothing better is left
        score += 2 if any(n["href"].rstrip("/") == href for n in data["nav"]) else 0
        score -= href.count("/") - 3  # prefer shallow pages
        scored[href] = max(score, scored.get(href, -99))
    return [u for u, _ in sorted(scored.items(), key=lambda kv: -kv[1])][:limit]


# --------------------------------------------------------------------------- analysis

def analyse_colors(pages):
    text, bg, border, btn = Counter(), Counter(), Counter(), Counter()
    for p in pages:
        for k, v in normalise(p["textColors"]).items():
            text[k] += v
        for k, v in normalise(p["bgColors"]).items():
            bg[k] += v
        for k, v in normalise(p["borderColors"]).items():
            border[k] += v
        for b in p["buttons"]:
            for k in (b["bg"], b["color"], b["border"]):
                if k:
                    btn[k] += 1
        for r in ("link", "h1", "h2", "button"):
            c = p["roles"].get(r, {}).get("color")
            if c:
                btn[c] += 0.5
    combined = Counter()
    for src, weight in ((bg, 1.0), (text, 0.8), (border, 0.3), (normalise(btn), 1.2)):
        for k, v in normalise(src).items():
            combined[k] += v * weight
    clusters = merge_colors(combined)
    for cl in clusters:
        cl["name"] = color_name(cl["hex"])
        cl["saturation"] = round(saturation(cl["hex"]), 2)
        cl["share"] = round(cl["weight"] * 100 / (sum(c["weight"] for c in clusters) or 1), 1)
        cl["uses"] = [u for u, src in (("background", bg), ("text", text), ("border", border), ("buttons/links", btn))
                      if any(v in src for v in cl["variants"])]
    brand = [c for c in clusters if c["saturation"] > 0.18][:6]
    neutrals = [c for c in clusters if c["saturation"] <= 0.18][:6]
    return {"brand": brand, "neutrals": neutrals, "all": clusters[:24]}


def analyse_fonts(pages):
    fams = defaultdict(lambda: {"chars": 0, "weights": Counter(), "sizes": Counter(), "tags": Counter(), "stack": ""})
    for p in pages:
        for stack, f in p["fonts"].items():
            name = first_font(stack)
            e = fams[name]
            e["stack"] = e["stack"] or stack
            e["chars"] += f["chars"]
            e["weights"].update(f["weights"])
            e["sizes"].update(f["sizes"])
            e["tags"].update(f["tags"])
    total = sum(f["chars"] for f in fams.values()) or 1
    out = []
    for name, f in sorted(fams.items(), key=lambda kv: -kv[1]["chars"]):
        out.append({"family": name, "stack": f["stack"], "share": round(f["chars"] * 100 / total, 1),
                    "weights": [w for w, _ in f["weights"].most_common(5)],
                    "sizes": [s for s, _ in f["sizes"].most_common(6)],
                    "used_on": [t for t, _ in f["tags"].most_common(6)]})
    sources = sorted({l for p in pages for l in p["fontLinks"]})
    faces = {}
    for p in pages:
        for ff in p["fontFaces"]:
            faces.setdefault(ff["family"], ff["src"])
    loaded = sorted({f["family"] for p in pages for f in p["loadedFonts"]})
    return {"families": out[:8], "sources": sources, "font_faces": faces, "loaded": loaded}


def walk_jsonld(node, found):
    if isinstance(node, list):
        for n in node:
            walk_jsonld(n, found)
    elif isinstance(node, dict):
        if "@type" in node:
            found.append(node)
        for v in node.values():
            if isinstance(v, (dict, list)):
                walk_jsonld(v, found)


def fmt_address(a):
    if isinstance(a, str):
        return a
    if isinstance(a, list):
        return "; ".join(fmt_address(x) for x in a)
    if isinstance(a, dict):
        keys = ["streetAddress", "addressLocality", "addressRegion", "postalCode", "addressCountry"]
        parts = []
        for k in keys:
            v = a.get(k)
            if isinstance(v, dict):
                v = v.get("name")
            if v:
                parts.append(str(v))
        return ", ".join(parts)
    return ""


def analyse_business(pages, home):
    info = {"name": None, "types": [], "description": None, "addresses": [], "phones": [], "emails": [],
            "hours": [], "socials": {}, "price_range": None, "area_served": None, "founded": None,
            "platform": None, "category_guess": [], "structured_data": []}
    meta = home["meta"]
    info["name"] = meta.get("og:site_name") or meta.get("application-name")
    info["description"] = meta.get("description") or meta.get("og:description")

    entities = []
    for p in pages:
        for j in p["jsonld"]:
            walk_jsonld(j, entities)
    skip_types = {"WebPage", "WebSite", "BreadcrumbList", "ListItem", "SearchAction", "ImageObject",
                  "EntryPoint", "ReadAction", "CollectionPage", "SiteNavigationElement", "PropertyValueSpecification"}
    for e in entities:
        types = e["@type"] if isinstance(e["@type"], list) else [e["@type"]]
        if all(t in skip_types for t in types):
            continue
        info["structured_data"].append({k: v for k, v in e.items() if not k.startswith("@context")})
        info["types"] += [t for t in types if t not in info["types"] and t not in skip_types]
        if e.get("name") and not info["name"] and isinstance(e["name"], str):
            info["name"] = e["name"]
        if e.get("address"):
            info["addresses"].append(fmt_address(e["address"]))
        for k, dest in (("telephone", "phones"), ("email", "emails")):
            if isinstance(e.get(k), str):
                info[dest].append(e[k])
        oh = e.get("openingHours") or e.get("openingHoursSpecification")
        if oh:
            info["hours"].append(json.dumps(oh) if not isinstance(oh, str) else oh)
        info["price_range"] = info["price_range"] or e.get("priceRange")
        info["area_served"] = info["area_served"] or e.get("areaServed")
        info["founded"] = info["founded"] or e.get("foundingDate")
        for s in e.get("sameAs") or []:
            if isinstance(s, str):
                info["socials"].setdefault(next((n for d, n in SOCIAL.items() if d in s), s), s)

    all_text = "\n".join(p["text"] for p in pages)
    for p in pages:
        for l in p["links"]:
            h = l.get("href") or ""
            if h.startswith("tel:"):
                info["phones"].append(re.sub(r"^tel:", "", h).strip())
            elif h.startswith("mailto:"):
                info["emails"].append(h[7:].split("?")[0].strip())
            else:
                for d, n in SOCIAL.items():
                    if d in h and "share" not in h and "sharer" not in h:
                        info["socials"].setdefault(n, h)
    info["emails"] += re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+\w", all_text)
    info["phones"] += re.findall(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}", all_text)[:5]

    # Address patterns: AU (state + postcode), UK postcode, US (state + ZIP), NZ
    addr_res = [r"\b\d{1,5}[A-Za-z]?(?:[-/]\d+)?\s+(?:[A-Z][\w'’.-]*\s+){1,4}(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|"
                r"Boulevard|Blvd|Parade|Pde|Highway|Hwy|Terrace|Tce|Place|Pl|Crescent|Cres|Court|Ct|Way|Close|Square|Sq)\b\.?[^\n]{0,60}",
                r"[^\n]{0,80}\b(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)\s*,?\s*\d{4}\b[^\n]{0,20}",
                r"[^\n]{0,80}\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b",
                r"[^\n]{0,80}\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
                r"[^\n]{0,80}\b(Auckland|Wellington|Christchurch|Hamilton|Tauranga|Dunedin)\s+\d{4}\b"]
    for rx in addr_res:
        for m in re.finditer(rx, all_text):
            a = m.group(0)
            if m.start() > 0 and all_text[m.start() - 1].isalnum():
                a = a.split(" ", 1)[-1]  # drop the cut-off partial word at the start
            info["addresses"].append(a.strip(" ,|•-"))
    for line in all_text.splitlines():
        if re.search(DAYS, line, re.I) and re.search(r"\d{1,2}(:\d{2})?\s*(am|pm)|\d{1,2}:\d{2}|closed", line, re.I) and len(line) < 120:
            info["hours"].append(line.strip())

    def dedupe(xs, key=lambda x: re.sub(r"\W", "", x.lower())):
        seen, out = set(), []
        for x in xs:
            k = key(x)
            if x and k and k not in seen:
                seen.add(k)
                out.append(x.strip())
        return out
    addrs = dedupe(info["addresses"])
    squash = lambda x: re.sub(r"\W", "", x.lower())
    info["addresses"] = [a for a in addrs if not any(squash(a) in squash(b) and a != b for b in addrs)][:6]
    info["phones"] = dedupe([p for p in info["phones"] if len(re.sub(r"\D", "", p)) >= 8],
                            key=lambda x: re.sub(r"\D", "", x)[-9:])[:5]
    info["emails"] = dedupe([e for e in info["emails"] if not re.search(r"\.(png|jpe?g|webp|svg|gif)$|sentry|wixpress|example\.", e, re.I)])[:5]
    info["hours"] = dedupe(info["hours"])[:14]

    html_hint = json.dumps(home["meta"]).lower() + " ".join(i["url"] or "" for i in home["images"][:40]).lower()
    gen = home["meta"].get("generator")
    info["platform"] = gen or next((n for k, n in PLATFORMS.items() if k in html_hint), None)

    low = all_text.lower()
    scores = {cat: sum(low.count(w) for w in words) for cat, words in BUSINESS_KEYWORDS.items()}
    info["category_guess"] = [c for c, s in sorted(scores.items(), key=lambda kv: -kv[1]) if s >= 3][:3]
    return info


# --------------------------------------------------------------------------- downloads

def download(ctx, url, dest_dir, stem, seen_hashes, min_px=40):
    """Download url into dest_dir. Returns (path, width, height) or None."""
    try:
        if url.startswith("data:"):
            return None
        r = ctx.request.get(url, timeout=25000, headers={"Referer": url})
        if not r.ok:
            return None
        body = r.body()
        if len(body) < 200 or len(body) > 20_000_000:
            return None
        h = hashlib.md5(body).hexdigest()
        if h in seen_hashes:
            return None
        ctype = (r.headers.get("content-type") or "").split(";")[0]
        ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
               "image/svg+xml": ".svg", "image/avif": ".avif", "image/x-icon": ".ico",
               "image/vnd.microsoft.icon": ".ico"}.get(ctype)
        if not ext:
            ext = Path(urlparse(url).path).suffix.lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".avif", ".ico"):
                return None
        w = hgt = 0
        if ext != ".svg":
            try:
                im = Image.open(io.BytesIO(body))
                w, hgt = im.size
            except Exception:
                pass
            if w and (w < min_px or hgt < min_px):
                return None
        seen_hashes.add(h)
        path = dest_dir / f"{stem}{ext}"
        path.write_bytes(body)
        return path, w, hgt
    except Exception:
        return None


def image_palette(path, n=5):
    """Dominant colours of an image, ignoring transparency and the edge/background colour."""
    try:
        im = Image.open(path).convert("RGBA")
        im.thumbnail((200, 200))
        raw = im.tobytes()
        px = [tuple(raw[i:i + 4]) for i in range(0, len(raw), 4) if raw[i + 3] > 128]
        if not px:
            return []
        w, h = im.size
        corners = [im.getpixel(xy) for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        bgc = Counter(rgb_to_hex(c) for c in corners if c[3] > 128).most_common(1)
        bg_hex = bgc[0][0] if bgc and bgc[0][1] >= 3 else None
        counts = Counter(rgb_to_hex((p[0] // 8 * 8, p[1] // 8 * 8, p[2] // 8 * 8)) for p in px)
        clusters = merge_colors(dict(counts), threshold=40)
        total = sum(c["weight"] for c in clusters)
        out = []
        for c in clusters:
            if bg_hex and color_dist(c["hex"], bg_hex) < 40:
                continue
            if c["weight"] / total < 0.02:
                continue
            out.append(c["hex"])
        return out[:n]
    except Exception:
        return []


def make_palette_png(colors, path):
    groups = [("Brand / accent colours", colors["brand"]), ("Neutrals", colors["neutrals"])]
    if colors.get("logo"):
        groups.insert(0, ("Logo colours", [{"hex": h, "name": color_name(h), "share": None} for h in colors["logo"]]))
    sw, pad, row_h = 170, 20, 230
    width = pad + max(1, max(len(g) for _, g in groups)) * (sw + pad)
    img = Image.new("RGB", (width, pad + len(groups) * row_h), "white")
    d = ImageDraw.Draw(img)
    try:
        f_big, f_small = ImageFont.truetype("arial.ttf", 20), ImageFont.truetype("arial.ttf", 15)
    except Exception:
        f_big = f_small = ImageFont.load_default()
    y = pad
    for title, cs in groups:
        d.text((pad, y), title, fill="#222", font=f_big)
        for i, c in enumerate(cs):
            x = pad + i * (sw + pad)
            d.rectangle([x, y + 32, x + sw, y + 152], fill=c["hex"], outline="#ccc")
            d.text((x, y + 160), c["hex"].upper(), fill="#222", font=f_big)
            sub = c["name"] + (f"  ·  {c['share']}%" if c.get("share") is not None else "")
            d.text((x, y + 186), sub, fill="#666", font=f_small)
        y += row_h
    img.save(path)


# --------------------------------------------------------------------------- report

def write_report(out, url, home, pages, colors, fonts, biz, logos, images, shots, vars_):
    L = []
    w = L.append
    name = business_name(biz, home, url)
    w(f"# Brand & Website Audit: {name}\n")
    w(f"- **Source URL:** {url}")
    w(f"- **Scraped:** {datetime.now():%d %B %Y, %H:%M}")
    w(f"- **Pages analysed:** {len(pages)}")
    if biz["platform"]:
        w(f"- **Current platform:** {biz['platform']}")
    w("\n> This document was generated automatically to brief a brand-identity refresh and website rebuild. "
      "Colours and fonts are measured from the live rendered site; business details come from structured data, "
      "contact links and page text, so verify anything important.\n")

    w("## 1. Business overview\n")
    w(f"- **Business name:** {name}")
    if biz["types"]:
        w(f"- **Business type (from site's structured data):** {', '.join(biz['types'])}")
    if biz["category_guess"]:
        w(f"- **Likely industry (keyword analysis):** {', '.join(biz['category_guess'])}")
    w(f"- **Page title:** {home['meta'].get('title', '')}")
    if biz["description"]:
        w(f"- **Meta description / elevator pitch:** {biz['description']}")
    for k, label in (("price_range", "Price range"), ("area_served", "Area served"), ("founded", "Founded")):
        if biz[k]:
            w(f"- **{label}:** {biz[k] if isinstance(biz[k], str) else json.dumps(biz[k])}")
    w("")

    w("## 2. Contact & location\n")
    w("**Address(es):** " + ("\n" + "\n".join(f"- {a}" for a in biz["addresses"]) if biz["addresses"] else "_none found_"))
    w("\n**Phone:** " + (", ".join(biz["phones"]) or "_none found_"))
    w("\n**Email:** " + (", ".join(biz["emails"]) or "_none found_"))
    if biz["hours"]:
        w("\n**Opening hours:**\n" + "\n".join(f"- {h}" for h in biz["hours"]))
    if biz["socials"]:
        w("\n**Social / listings:**\n" + "\n".join(f"- {k}: {v}" for k, v in biz["socials"].items()))
    w("")

    w("## 3. Logo\n")
    if logos:
        for lg in logos:
            w(f"- `{lg['file']}` — {lg['desc']}")
        if colors.get("logo"):
            w(f"\n**Colours in the logo:** {', '.join(f'`{c}` ({color_name(c)})' for c in colors['logo'])}")
        w(f"\n![Logo]({logos[0]['file']})")
    else:
        w("_No logo detected automatically. Check the screenshots and images folder._")
    icons = [i for p in pages[:1] for i in p["icons"]]
    if icons:
        w("\n**Favicons / app icons:** " + ", ".join(f"[{i['rel']}{' ' + i['sizes'] if i['sizes'] else ''}]({i['href']})" for i in icons))
    w("")

    w("## 4. Colour palette\n")
    w("![Palette](palette.png)\n")
    w("### Brand / accent colours (saturated, most used first)\n")
    w("| Hex | Description | Share of site | Used for |\n|---|---|---|---|")
    for c in colors["brand"]:
        w(f"| `{c['hex']}` | {c['name']} | {c['share']}% | {', '.join(c['uses'])} |")
    w("\n### Neutrals (backgrounds, text, borders)\n")
    w("| Hex | Description | Share of site | Used for |\n|---|---|---|---|")
    for c in colors["neutrals"]:
        w(f"| `{c['hex']}` | {c['name']} | {c['share']}% | {', '.join(c['uses'])} |")
    theme = home["meta"].get("theme-color")
    if theme:
        w(f"\n**Declared theme colour (meta tag):** `{theme}`")
    color_vars = {k: v for k, v in vars_.items() if v.get("hex")}
    if color_vars:
        w("\n### CSS colour variables defined by the site\n")
        w("\n".join(f"- `{k}`: `{v['hex']}` ({color_name(v['hex'])})" for k, v in list(color_vars.items())[:40]))
    grads = sorted({g for p in pages for g in p["gradients"]})[:5]
    if grads:
        w("\n**Gradients used:**\n" + "\n".join(f"- `{g}`" for g in grads))
    roles = home["roles"]
    w("")

    w("## 5. Typography\n")
    w("| Font family | Share of text | Weights | Common sizes | Used on |\n|---|---|---|---|---|")
    for f in fonts["families"]:
        w(f"| **{f['family']}** | {f['share']}% | {', '.join(f['weights'])} | {', '.join(f['sizes'])} | {', '.join(f['used_on'])} |")
    w("\n### Type styles by role (homepage)\n")
    w("| Role | Font | Size | Weight | Line height | Letter spacing | Transform | Colour | Sample |\n|---|---|---|---|---|---|---|---|---|")
    for role in ("h1", "h2", "h3", "h4", "body", "link", "nav", "button", "footer"):
        s = roles.get(role)
        if s:
            w(f"| {role} | {first_font(s['fontFamily'])} | {s['fontSize']} | {s['fontWeight']} | {s['lineHeight']} | "
              f"{s['letterSpacing']} | {s['textTransform']} | `{s['color']}` | {s['sample'][:40].replace('|', '/')} |")
    if fonts["sources"]:
        w("\n**Font sources:**\n" + "\n".join(f"- {s}" for s in fonts["sources"]))
    if fonts["loaded"]:
        w("\n**Web fonts actually loaded:** " + ", ".join(fonts["loaded"]))
    w("")

    w("## 6. UI components\n")
    styles = Counter()
    examples = {}
    for p in pages:
        for b in p["buttons"]:
            k = (b["bg"], b["color"], b["radius"], b["border"] if b["borderWidth"] not in ("0px", "") else None)
            styles[k] += 1
            examples.setdefault(k, b)
    if styles:
        w("**Button styles found** (most common first):\n")
        w("| Background | Text colour | Border | Corner radius | Font weight | Transform | Example |\n|---|---|---|---|---|---|---|")
        for k, n in styles.most_common(6):
            b = examples[k]
            w(f"| `{b['bg'] or 'transparent'}` | `{b['color']}` | {('`' + str(k[3]) + '` ' + b['borderWidth']) if k[3] else 'none'} | "
              f"{b['radius']} | {b['fontWeight']} | {b['textTransform']} | \"{b['text'][:30]}\" |")
    w("")

    w("## 7. Site structure\n")
    nav = []
    seen = set()
    for n in home["nav"]:
        if n["text"] not in seen and len(n["text"]) < 40:
            seen.add(n["text"])
            nav.append(n)
    if nav:
        w("**Main navigation:** " + " · ".join(n["text"] for n in nav[:20]))
    w("\n**Pages crawled:**")
    for p in pages:
        w(f"- [{p['meta'].get('title') or p['url']}]({p['url']}) → `pages/{p['_file']}`")
    w("")

    w("## 8. Key messaging & content\n")
    w("_Headings in page order — these show what the business emphasises (services, USPs, taglines)._\n")
    for p in pages:
        hs = p["headings"][:25]
        if hs:
            w(f"**{p['meta'].get('title') or p['url']}**")
            for h in hs:
                indent = "  " * (int(h["level"][1]) - 1)
                w(f"{indent}- {h['text']}")
            w("")
    ctas = Counter(b["text"] for p in pages for b in p["buttons"] if 2 < len(b["text"]) < 40)
    if ctas:
        w("**Calls to action:** " + ", ".join(f"\"{t}\"" for t, _ in ctas.most_common(12)) + "\n")
    tests = [t for p in pages for t in p["testimonials"]][:8]
    if tests:
        w("### Testimonials / reviews found\n")
        for t in tests:
            w(f"> {t}\n")

    w("## 9. Special features & functionality detected\n")
    feats = detect_features(pages)
    w("\n".join(f"- {f}" for f in feats) if feats else "_Nothing notable detected._")
    w("")

    w("## 10. Imagery\n")
    w(f"{len(images)} images downloaded to `images/` (manifest: `images/images.csv`). Largest / most prominent:\n")
    for im in sorted(images, key=lambda i: -(i["width"] * i["height"]))[:12]:
        w(f"- `{im['file']}` — {im['width']}×{im['height']}{' — ' + im['alt'] if im['alt'] else ''}")
    w("\n### Screenshots\n")
    for s in shots:
        w(f"- `{s}`")
    if shots:
        w(f"\n![Homepage]({shots[0]})")
    w("")

    w("## 11. Brief for redesign (for Claude Design)\n")
    w("Use this audit to propose an improved brand identity and website. Suggested prompt:\n")
    w("> Here is an automated audit of an existing business website (brand-report.md, palette.png, logo files, "
      "screenshots and images). Please (1) summarise the current brand personality and its weaknesses, "
      "(2) propose a refined brand identity — logo direction, a primary/secondary/neutral colour palette with hex "
      "codes and accessible contrast, and a heading/body font pairing, (3) propose a sitemap and homepage layout "
      "that keeps the business's key services, contact details and differentiators, and (4) design the new homepage.\n")
    body = roles.get("body", {})
    bg_main = colors["neutrals"][0]["hex"] if colors["neutrals"] else "#ffffff"
    if body.get("color"):
        cr = contrast(body["color"], bg_main)
        w(f"- Body text contrast (`{body['color']}` on `{bg_main}`): **{cr:.1f}:1** "
          f"({'passes' if cr >= 4.5 else 'FAILS'} WCAG AA)")
    w(f"- Distinct font families in use: {len(fonts['families'])}"
      + (" (consider consolidating to 2)" if len(fonts["families"]) > 3 else ""))
    w(f"- Distinct saturated colours in use: {len(colors['brand'])}"
      + (" (palette may be unfocused)" if len(colors["brand"]) > 4 else ""))
    (out / "brand-report.md").write_text("\n".join(L), encoding="utf-8")


def business_name(biz, home, url):
    return (biz["name"] or re.split(r"\s[|–—-]\s", home["meta"].get("title", ""))[0].strip()
            or urlparse(url).netloc)


# --------------------------------------------------------------------------- Claude Design bundle

def save_resized(src, dest, max_edge=2000):
    """Copy an image, shrinking very large photos so uploads stay light."""
    try:
        im = Image.open(src)
        if max(im.size) <= max_edge and src.stat().st_size < 3_000_000:
            dest.write_bytes(src.read_bytes())
            return dest
        im.thumbnail((max_edge, max_edge))
        dest = dest.with_suffix(".jpg")
        im.convert("RGB").save(dest, "JPEG", quality=85)
        return dest
    except Exception:
        return None


def save_tall_screenshot(src, dest_stem, width, max_h=5000):
    """Scale a full-page screenshot to `width` and split it into parts upload tools accept, as JPEG."""
    try:
        im = Image.open(src).convert("RGB")
    except Exception:
        return []
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    h = im.height
    n = max(1, -(-h // max_h))  # ceil
    step = -(-h // n)
    parts = []
    for i in range(n):
        dest = dest_stem.parent / (f"{dest_stem.name}-part{i + 1}.jpg" if n > 1 else f"{dest_stem.name}.jpg")
        im.crop((0, i * step, im.width, min((i + 1) * step, h))).save(dest, "JPEG", quality=82)
        parts.append(dest)
    return parts


def pick_photos(images, logos, n=10):
    logo_urls = {l.get("file") for l in logos}
    out, seen_pages = [], Counter()
    for im in sorted(images, key=lambda i: -(i["width"] * i["height"])):
        w, h = im["width"], im["height"]
        if not w or min(w, h) < 500 or max(w, h) / min(w, h) > 3.5:
            continue
        if re.search(r"logo|icon|badge|sprite|placeholder", im["file"] + " " + im["alt"], re.I) or im["file"] in logo_urls:
            continue
        if seen_pages[im["found_on"]] >= 4:  # spread picks across pages
            continue
        seen_pages[im["found_on"]] += 1
        out.append(im)
        if len(out) >= n:
            break
    return out


def best_logo(logos):
    files = [l["file"] for l in logos]
    for pattern in (r"logo-[12]\.svg$", r"logo-1-original\.(png|webp|jpg)$", r"logo-\d\.svg$",
                    r"logo-1-rendered\.png$", r"logo-\d-original", r"rendered\.png$"):
        hit = next((f for f in files if re.search(pattern, f)), None)
        if hit:
            return hit
    return None


def make_upload_bundle(out, url, pages, biz, logos, images, colors, fonts):
    """Build claude-design-upload/: just the files worth uploading, plus pre-filled prompts."""
    b = out / "claude-design-upload"
    if b.exists():
        for f in b.iterdir():
            f.unlink()
    b.mkdir(exist_ok=True)
    home = pages[0]
    files = []

    def copy(src, name):
        dest = b / name
        dest.write_bytes(src.read_bytes())
        files.append(dest.name)

    copy(out / "brand-report.md", "01-brand-report.md")
    copy(out / "palette.png", "02-current-palette.png")
    lg = best_logo(logos)
    if lg:
        copy(out / lg, "03-current-logo" + Path(lg).suffix)
        rendered = next((l["file"] for l in logos if l["file"].endswith("rendered.png")), None)
        if Path(lg).suffix == ".svg" and rendered:  # PNG too, in case SVG uploads aren't accepted
            copy(out / rendered, "03-current-logo.png")
    for src, stem, width in (("home-desktop-full.png", "04-current-site-desktop", 1440),
                             ("home-mobile-full.png", "05-current-site-mobile", 390)):
        if (out / "screenshots" / src).exists():
            files += [p.name for p in save_tall_screenshot(out / "screenshots" / src, b / stem, width)]
    photos = pick_photos(images, logos)
    for i, im in enumerate(photos, 1):
        dest = save_resized(out / im["file"], b / f"06-photo-{i:02d}{Path(im['file']).suffix}")
        if dest:
            files.append(dest.name)
    key_pages = [p for p in pages[1:] if re.search(r"about|story|service|menu|product|what-we", p["url"], re.I)][:3]
    if key_pages or pages:
        parts = [(out / "pages" / p["_file"]).read_text(encoding="utf-8") for p in [home] + key_pages]
        (b / "07-page-content.md").write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        files.append("07-page-content.md")

    # ---- pre-filled prompts ----
    name = business_name(biz, home, url)
    btype = ", ".join(biz["types"][:2] + biz["category_guess"][:1]) or "[type of business]"
    if len(biz["addresses"]) > 1:
        location = f"{len(biz['addresses'])} locations including {biz['addresses'][0]}"
    else:
        location = biz["addresses"][0] if biz["addresses"] else "[location]"
    ctas = Counter(bt["text"] for p in pages for bt in p["buttons"] if 2 < len(bt["text"]) < 30)
    main_cta = next((t for t, _ in ctas.most_common(10)
                     if re.search(r"order|book|shop|contact|quote|call|buy|enquir|get|start|visit|reserve", t, re.I)), None)
    feats = [re.sub(r"\s*_\(matched.*", "", f) for f in detect_features(pages)]
    keep = feats[:6] + ([f"all {len(biz['addresses'])} locations"] if len(biz["addresses"]) > 1 else [])
    fonts_now = ", ".join(f["family"] for f in fonts["families"][:2])
    cur = (colors.get("logo") or [])[:2] + [c["hex"] for c in colors["brand"][:3]] + [c["hex"] for c in colors["neutrals"][:2]]
    brand_now = ", ".join(c["hex"] for c in merge_colors({h: len(cur) - i for i, h in enumerate(cur)}))

    P = f"""# Claude Design prompts: {name}

Generated from {url} on {datetime.now():%d %B %Y}.

## Files to upload

Upload everything in this folder ({len(files)} files):

{chr(10).join(f"- `{f}`" for f in files)}

Run the prompts below **one at a time**. Review each result, give feedback, and only move on when you're happy.
Anything still in [square brackets] is for you to fill in, usually what the client wants from the refresh.

---

## Prompt 1: Audit (no designing yet)

```
I'm redesigning the brand and website for {name}, a {btype} business ({location}).
Attached is an automated audit of their current website: a brand report, colour palette, logo,
screenshots of the current site, photos, and the text from key pages.

The client's goals for the refresh: [e.g. feel more premium / modern / friendly, attract a younger audience,
get more online orders]. They want to keep: [e.g. the logo symbol, the brown colour].

Before designing anything, give me:
1. A summary of the business: what they sell, who their customers are, what makes them different
2. The current brand personality in 3–5 words
3. What's working in the current brand and site and should be kept
4. The biggest weaknesses (visual, messaging, usability, mobile, accessibility)
Don't design anything yet.
```

> Check this carefully and correct anything it gets wrong about the business. Everything after builds on it.

## Prompt 2: Brand directions

```
Based on that audit, propose 3 distinct brand identity directions for the refresh. For each give:
- A name and a one-line concept
- Personality keywords
- Logo direction (evolve the current logo, or new)
- A colour palette: primary, secondary, accent and neutrals with hex codes, checked for WCAG AA contrast
- A heading and body font pairing (Google Fonts where possible)
- A sample hero section showing it in use
The current brand uses {fonts_now or "[fonts]"} and the colours {brand_now or "[colours]"}.
Keep at least one direction close to the existing brand, for customers who already know it.
```

## Prompt 3: Lock in the brand and build a design system

```
Let's go with direction [X], with these changes: [your tweaks].
Now create a complete brand and design system for {name}:
- Final logo (primary, horizontal, stacked and icon-only versions)
- Colour palette with usage rules (what each colour is for, and when not to use it)
- Type scale (H1–H4, body, small, button) with sizes, weights and line heights
- Buttons (primary, secondary, text link), form fields, cards, and navigation style
- Spacing, corner radius, and photography/illustration style guidance
- Tone of voice: 3 principles, with do and don't examples
```

## Prompt 4: Sitemap and content plan

```
Using the brand report's navigation, page content and detected features, propose a sitemap
for the new {name} website. For each page, list its sections in order and the goal of the page.
Keep everything customers currently rely on ({"; ".join(keep) or "[key features]"}),
consolidate or cut anything redundant, and make the main call to action
("{main_cta or "[main call to action]"}") easy to find on every page.
```

## Prompt 5: Homepage

```
Design the homepage using the design system and sitemap. Use the real business details, addresses
and photos I uploaded, not placeholders. Rewrite the copy to be clearer and more persuasive, in the
brand's tone of voice. Show desktop and mobile versions.
```

## Prompt 6: Other pages (repeat for each page in the sitemap)

```
Now design the [About / Services / Menu / Contact / Locations] page in the same design system.
[Any specific content or requirements for this page.]
```

## Prompt 7: Review pass

```
Review all the pages together for consistency, accessibility (contrast, font sizes, tap targets
on mobile), and conversion. List issues and fix them.
```

---

## Building the site

When the designs are final, hand them off to Claude Code and ask something like:

```
Build this website from these designs as [a static site / Next.js / WordPress theme], fully responsive,
using the real images from {out.resolve() / "images"} and the page text in {out.resolve() / "pages"}.
```
"""
    (b / "00-PROMPTS.md").write_text(P, encoding="utf-8")
    return b


def detect_features(pages):
    text = " ".join(p["text"].lower() for p in pages)
    hrefs = " ".join((l.get("href") or "").lower() for p in pages for l in p["links"])
    checks = [
        ("Online booking / appointments", r"book (now|online|an appointment)|schedule (a|an|your)|calendly|fresha|mindbody|setmore|square(up)? appointments|timely", text + hrefs),
        ("Online ordering / e-commerce", r"add to cart|add to bag|checkout|shop now|order online|ubereats|doordash|menulog|deliveroo", text + hrefs),
        ("Contact / enquiry form", r"<form|send message|submit|enquir|get a quote|request a quote", text),
        ("Free quote / estimate offer", r"free (quote|estimate|consultation|assessment|trial)", text),
        ("Newsletter signup", r"newsletter|subscribe", text),
        ("Blog / news", r"/blog|/news|/articles|/insights", hrefs),
        ("Gallery / portfolio", r"gallery|portfolio|our work|projects", text + hrefs),
        ("Testimonials / reviews", r"testimonial|review|what (our )?(clients|customers) say|★|google rating", text),
        ("Pricing / packages", r"pricing|packages|\$\d+|£\d+|€\d+|per (hour|month|session)", text),
        ("FAQ section", r"\bfaq|frequently asked", text + hrefs),
        ("Team / staff profiles", r"our team|meet the team|our staff|our people", text),
        ("Awards / accreditations", r"award|accredit|certified|licensed|member of|registered", text),
        ("Gift cards / vouchers", r"gift (card|voucher|certificate)", text),
        ("Careers / hiring", r"careers|we're hiring|join our team|vacanc", text + hrefs),
        ("Emergency / 24-7 service", r"24/7|24 hours|emergency", text),
        ("Live chat / messaging widget", r"intercom|tawk\.to|drift|livechat|crisp\.chat|messenger|wa\.me|whatsapp", hrefs + text),
        ("Embedded map", r"google\.com/maps|maps\.google|goo\.gl/maps|maps\.app", hrefs),
        ("Video content", r"youtube\.com/embed|vimeo|<video|watch (the|our) video", hrefs + text),
        ("Multiple locations", r"our locations|find a (store|location)|branches", text),
        ("Loyalty / membership", r"loyalty|membership|rewards|members", text),
        ("Finance / payment plans", r"afterpay|zip pay|klarna|payment plan|finance options|humm", text + hrefs),
    ]
    found = []
    for label, rx, hay in checks:
        m = re.search(rx, hay)
        if m:
            found.append(f"{label} _(matched “{m.group(0)[:40]}”)_")
    return found


# --------------------------------------------------------------------------- main

def run(url, max_pages, max_images, out_root, headed=False):
    if not url.startswith("http"):
        url = "https://" + url
    base_host = urlparse(url).netloc.lower().removeprefix("www.")
    out_root = Path(out_root)
    if not out_root.is_dir():
        out_root.mkdir(parents=True)
        log(f"Created output folder: {out_root.resolve()}")
    out = out_root / slug(base_host, 80)
    for sub in ("logo", "images", "screenshots", "pages"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    log(f"Output folder: {out.resolve()}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, user_agent=UA,
                                  device_scale_factor=1, ignore_https_errors=True)
        page = ctx.new_page()

        pages, queue, seen = [], [url], set()
        while queue and len(pages) < max_pages:
            u = queue.pop(0)
            key = urldefrag(u)[0].rstrip("/")
            if key in seen:
                continue
            seen.add(key)
            log(f"[{len(pages) + 1}/{max_pages}] {u}")
            try:
                load_page(page, u)
                data = page.evaluate(EXTRACT_JS)
            except Exception as e:
                log(f"   ! failed: {str(e).splitlines()[0]}")
                if not pages:
                    sys.exit("Could not load the homepage — check the URL.")
                continue
            final_key = urldefrag(page.url)[0].rstrip("/")
            if not same_site(page.url, base_host) and not pages:
                base_host = urlparse(page.url).netloc.lower().removeprefix("www.")
            seen.add(final_key)
            fname = f"{len(pages):02d}-{slug(urlparse(page.url).path) if urlparse(page.url).path.strip('/') else 'home'}.md"
            data["_file"] = fname
            (out / "pages" / fname).write_text(
                f"# {data['meta'].get('title', '')}\n\nURL: {page.url}\n\n{data['text']}", encoding="utf-8")

            if 1 <= len(pages) <= 5:  # above-the-fold shot while the page is already loaded
                try:
                    name = f"screenshots/page-{fname[:-3]}.png"
                    page.screenshot(path=out / name)
                    shots.append(name)
                except Exception:
                    pass

            if not pages:  # homepage extras: logos + screenshots
                shots = []
                page.screenshot(path=out / "screenshots/home-desktop-full.png", full_page=True)
                page.screenshot(path=out / "screenshots/home-desktop-viewport.png")
                shots += ["screenshots/home-desktop-viewport.png", "screenshots/home-desktop-full.png"]
                logos, seen_hashes = [], set()
                for lg in data["logos"]:
                    i = lg["index"]
                    loc = page.locator(f'[data-scrp-logo="{i}"]').first
                    rendered = out / f"logo/logo-{i + 1}-rendered.png"
                    try:
                        loc.screenshot(path=rendered, timeout=5000)
                        logos.append({"file": f"logo/{rendered.name}", "desc": f"rendered on page ({lg['w']}×{lg['h']}px, {lg['kind']}, confidence {lg['score']})"})
                    except Exception:
                        pass
                    if lg["kind"] == "svg" and lg["svg"]:
                        svg = lg["svg"]
                        if "xmlns=" not in svg:
                            svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                        (out / f"logo/logo-{i + 1}.svg").write_text(svg, encoding="utf-8")
                        logos.append({"file": f"logo/logo-{i + 1}.svg", "desc": "original inline SVG (vector)"})
                    elif lg["url"]:
                        res = download(ctx, lg["url"], out / "logo", f"logo-{i + 1}-original", seen_hashes, min_px=1)
                        if res:
                            logos.append({"file": f"logo/{res[0].name}", "desc": f"original file {res[1]}×{res[2]} from {lg['url']}"})
                for ic in data["icons"]:
                    if ic["href"] and "manifest" not in (ic["rel"] or ""):
                        res = download(ctx, ic["href"], out / "logo", f"icon-{slug(ic['rel'])}-{slug(ic['sizes'] or '')}", seen_hashes, min_px=1)
                        if res:
                            logos.append({"file": f"logo/{res[0].name}", "desc": f"{ic['rel']} {ic['sizes'] or ''}".strip()})
                og = data["meta"].get("og:image")
                if og:
                    res = download(ctx, og, out / "logo", "og-share-image", seen_hashes, min_px=1)
                    if res:
                        logos.append({"file": f"logo/{res[0].name}", "desc": "social share (og:image)"})

            pages.append(data)
            for link in pick_links(data, base_host, seen | {urldefrag(q)[0].rstrip('/') for q in queue}, max_pages * 2):
                if link not in queue:
                    queue.append(link)
            queue.sort(key=lambda q: -sum(w in q.lower() for w in PRIORITY_WORDS)
                       + (20 if re.search(r"terms|privacy|policy|cookie|legal|disclaimer|sitemap", q, re.I) else 0))

        # Mobile screenshot
        log("Taking mobile screenshot…")
        try:
            mctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                                       device_scale_factor=2, user_agent=UA.replace("Windows NT 10.0; Win64; x64", "iPhone; CPU iPhone OS 18_0 like Mac OS X") + " Mobile")
            mp = mctx.new_page()
            load_page(mp, url)
            mp.screenshot(path=out / "screenshots/home-mobile-full.png", full_page=True)
            shots.append("screenshots/home-mobile-full.png")
            mctx.close()
        except Exception as e:
            log(f"   ! mobile screenshot failed: {e}")

        # Images
        log("Downloading images…")
        candidates = {}
        for p in pages:
            for im in p["images"]:
                u = im.get("best") or im.get("url")
                if u and not u.startswith("data:"):
                    candidates.setdefault(u, {"alt": im["alt"], "page": p["url"], "fallback": im.get("url")})
            for im in p["bgImages"]:
                candidates.setdefault(im["url"], {"alt": "(CSS background)", "page": p["url"], "fallback": None})
        images, hashes = [], set()
        for n, (u, meta) in enumerate(candidates.items()):
            if len(images) >= max_images:
                break
            stem = f"{len(images) + 1:03d}-{slug(Path(urlparse(u).path).stem, 40)}"
            res = download(ctx, u, out / "images", stem, hashes)
            if not res and meta["fallback"] and meta["fallback"] != u:
                res = download(ctx, meta["fallback"], out / "images", stem, hashes)
            if res:
                images.append({"file": f"images/{res[0].name}", "width": res[1], "height": res[2],
                               "alt": meta["alt"], "source_url": u, "found_on": meta["page"]})
        with open(out / "images/images.csv", "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=["file", "width", "height", "alt", "source_url", "found_on"])
            wr.writeheader()
            wr.writerows(images)
        log(f"   {len(images)} images saved")
        browser.close()

    # Analysis
    log("Analysing brand…")
    home = pages[0]
    colors = analyse_colors(pages)
    # Logo colours: exact SVG fills if vector, else the original file, else the on-page render
    svg_cols = next((l["colors"] for l in home["logos"] if l.get("colors")), [])
    colors["logo"] = [c["hex"] for c in merge_colors({h: 10 - i for i, h in enumerate(svg_cols[:10])})][:6]
    if not colors["logo"]:
        orig = next((out / l["file"] for l in logos
                     if l["file"].startswith("logo/logo-1-original") and not l["file"].endswith(".svg")), None)
        rendered = next((out / l["file"] for l in logos if l["file"].endswith("rendered.png")), None)
        for f in (orig, rendered):
            if f:
                colors["logo"] = image_palette(f)
                if colors["logo"]:
                    break
    fonts = analyse_fonts(pages)
    biz = analyse_business(pages, home)
    vars_ = {}
    for p in pages:
        vars_.update(p["vars"])
    make_palette_png(colors, out / "palette.png")
    write_report(out, url, home, pages, colors, fonts, biz, logos, images, shots, vars_)

    slim_pages = [{k: v for k, v in p.items() if k not in ("text", "links")} for p in pages]
    (out / "data.json").write_text(json.dumps({
        "url": url, "scraped_at": datetime.now().isoformat(), "business": biz, "colors": colors,
        "fonts": fonts, "logos": logos, "images": images, "css_variables": vars_, "pages": slim_pages,
    }, indent=2, default=str), encoding="utf-8")
    bundle = make_upload_bundle(out, url, pages, biz, logos, images, colors, fonts)
    log(f"\nDone! Full report: {(out / 'brand-report.md').resolve()}")
    log(f"Ready for Claude Design: {bundle.resolve()} (start with 00-PROMPTS.md)")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Scrape a website's brand identity and business details.")
    ap.add_argument("url", nargs="?", help="Website URL")
    ap.add_argument("--pages", type=int, default=8, help="Max pages to crawl (default 8)")
    ap.add_argument("--max-images", type=int, default=150, help="Max images to download (default 150)")
    ap.add_argument("--out", default=str(HERE / "output"), help="Output folder")
    ap.add_argument("--headed", action="store_true", help="Show the browser window while scraping")
    a = ap.parse_args()
    target = a.url or input("Enter website URL: ").strip()
    if not target:
        sys.exit("No URL given.")
    run(target, a.pages, a.max_images, a.out, a.headed)
