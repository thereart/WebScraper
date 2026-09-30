# Website Brand Scraper

Enter a website URL. The tool opens the site in a real (headless) Chrome browser, crawls the key pages
(home, about, services, contact, menu…), and produces a brand and business brief ready to hand to
Claude Design for a brand refresh and website rebuild.

## Setup (once)

```
pip install -r requirements.txt
python -m playwright install chromium
```

## Run

Double-click **`scrape.bat`** and paste a URL, or from a terminal:

```
python scrape.py https://example.com
python scrape.py https://example.com --pages 12 --max-images 300
python scrape.py example.com --headed        # watch the browser work
```

| Option | Default | Meaning |
|---|---|---|
| `--pages` | 8 | Max pages to crawl (prioritises about/contact/services/menu/etc.) |
| `--max-images` | 150 | Max images to download |
| `--out` | `output/` | Where results go |
| `--headed` | off | Show the browser window |

## What you get — `output/<domain>/`

| File | Contents |
|---|---|
| `claude-design-upload/` | **Start here.** Only the files worth uploading to Claude Design (about 20 files, under 10 MB), plus `00-PROMPTS.md`: step-by-step prompts pre-filled with the business name, location, current fonts and colours, and key features |
| `brand-report.md` | **The main document**: business overview, contact details and addresses, opening hours, socials, logo, colour palette, typography, button styles, navigation, headings and messaging, testimonials, detected features, image index, and a ready-made Claude Design prompt |
| `palette.png` | Visual swatches: logo colours, brand/accent colours, neutrals |
| `logo/` | Logo candidates: original SVG/PNG files, on-page renders, favicons, social share image |
| `images/` | Every site image found (highest-resolution version available) + `images.csv` with alt text and source page |
| `screenshots/` | Full-page desktop and mobile homepage, plus the top of each crawled page |
| `pages/` | Full text of every crawled page (useful for copywriting) |
| `data.json` | All raw extracted data |

## How it works

- **Colours** are measured from the rendered page: backgrounds weighted by area, text by character count,
  plus buttons/links and borders. Similar shades are merged and split into saturated "brand" colours and
  neutrals. The site's own CSS colour variables (e.g. `--color-primary`) are listed too.
- **Fonts** come from the computed styles of every text element, plus the web fonts actually loaded
  and Google Fonts/Typekit links. The type scale (h1–h4, body, nav, button) is recorded.
- **Logo** is scored by its name/class, being in the header, linking to the homepage, and position.
  Inline SVG logos are saved as vectors and their exact fill colours are read.
- **Business details** come from schema.org structured data (business type, address, hours) when present,
  otherwise from `tel:`/`mailto:` links and address and opening-hours patterns (AU, NZ, UK, US and generic
  street formats) in the page text.
- **Cookie banners** are removed from the page (never accepted) so they don't skew styles or screenshots.

## Using the output with Claude Design

1. Open `claude-design-upload/00-PROMPTS.md`.
2. Upload every other file in `claude-design-upload/` to Claude Design.
3. Fill in the [bracketed] parts of Prompt 1 (the client's goals), then run the prompts one at a time:
   audit → 3 brand directions → design system → sitemap → homepage → other pages → review.
4. When the designs are final, bring them back to Claude Code to build the site (last section of the prompts file).

## Limitations

- Sites behind bot protection (e.g. Cloudflare challenges) may block the scraper.
- Business type and "special features" are heuristic; double-check them in the report.
- Only scrape sites you have permission to use (e.g. a client's own site). Images remain the owner's copyright.
