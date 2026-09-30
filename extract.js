// Runs inside the page (via Playwright) and returns everything we can learn
// from the rendered DOM: colours, fonts, images, logo candidates, content.
async () => {
  const cv = document.createElement('canvas').getContext('2d');
  const hexCache = {};
  // Normalise any CSS colour to #rrggbb (null if transparent/unparseable).
  const hex = (c) => {
    if (!c) return null;
    c = String(c).trim();
    if (c in hexCache) return hexCache[c];
    let out = null;
    if (c !== 'transparent' && c !== 'none' && c.length < 80) {
      cv.fillStyle = '#010203';
      cv.fillStyle = c;
      const v = cv.fillStyle;
      if (v !== '#010203' || /^#010203$/i.test(c)) {
        if (v.startsWith('#')) out = v.toLowerCase();
        else {
          const m = v.match(/rgba?\(([^)]+)\)/);
          if (m) {
            const p = m[1].split(',').map((x) => parseFloat(x));
            if (p[3] === undefined || p[3] >= 0.15)
              out = '#' + p.slice(0, 3).map((x) => Math.round(x).toString(16).padStart(2, '0')).join('');
          }
        }
      }
    }
    return (hexCache[c] = out);
  };
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const px = (v) => (Math.round(parseFloat(v) * 10) / 10 || 0) + 'px';
  const clean = (t, n = 300) => (t || '').replace(/\s+/g, ' ').trim().slice(0, n);
  const add = (obj, k, w) => { obj[k] = (obj[k] || 0) + w; };
  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && parseFloat(s.opacity) > 0;
  };

  const R = {
    url: location.href, textColors: {}, bgColors: {}, borderColors: {}, gradients: [],
    fonts: {}, images: [], bgImages: [], logos: [], vars: {}, roles: {}, buttons: [],
    headings: [], nav: [], links: [], testimonials: [], jsonld: [], meta: {}, icons: [],
    loadedFonts: [], fontLinks: [], fontFaces: [], text: '',
  };

  // ---------- logo scoring ----------
  const logoScore = (el) => {
    let s = 0;
    const p = el.parentElement;
    const hay = [el.getAttribute('src'), el.getAttribute('alt'), el.id, el.getAttribute('class'),
      el.getAttribute('aria-label'), el.getAttribute('title'),
      p && p.id, p && p.getAttribute('class'), p && p.getAttribute('aria-label')].join(' ');
    if (/logo/i.test(hay)) s += 6;
    if (/brand|site-title|custom-logo|wordmark/i.test(hay)) s += 3;
    if (el.closest('header, [role=banner], [class*=header i], [id*=header i], nav')) s += 3;
    const a = el.closest('a');
    if (a) {
      try {
        const u = new URL(a.getAttribute('href') || '', location.href);
        if (u.origin === location.origin && /^\/(index\.\w+|home\/?)?$/i.test(u.pathname)) s += 4;
      } catch (e) {}
    }
    const r = el.getBoundingClientRect();
    if (r.top + scrollY < 250) s += 2;
    if (r.top + scrollY > 900 && !el.closest('footer')) s -= 6; // partner/sponsor grids further down the page
    if (r.width > 600 || r.height > 250) s -= 4;
    if (r.width < 16 || r.height < 10) s -= 4;
    if (/sponsor|partner|client|award|payment|badge|social|facebook|instagram|twitter|linkedin|icon-/i.test(hay)) s -= 4;
    return s;
  };
  const logoEls = [];
  const addLogo = (el, info) => {
    const score = logoScore(el);
    if (score >= 5) logoEls.push({ el, score, ...info });
  };

  // ---------- walk all visible elements ----------
  const vw = innerWidth;
  const docH = Math.min(document.documentElement.scrollHeight, 20000);
  for (const root of [document.documentElement, document.body]) {
    if (!root) continue;
    const bg = hex(getComputedStyle(root).backgroundColor);
    if (bg) add(R.bgColors, bg, vw * docH);
  }
  const els = document.body ? document.body.querySelectorAll('*') : [];
  for (const el of els) {
    const tag = el.tagName.toLowerCase();
    if (['script', 'style', 'noscript', 'template', 'meta', 'link', 'br'].includes(tag)) continue;
    if (el.closest('svg') && tag !== 'svg') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) === 0) continue;

    let txt = 0;
    for (const n of el.childNodes) if (n.nodeType === 3) txt += n.textContent.trim().length;
    if (txt) {
      const c = hex(s.color);
      if (c) add(R.textColors, c, txt);
      const f = R.fonts[s.fontFamily] || (R.fonts[s.fontFamily] = { chars: 0, weights: {}, sizes: {}, tags: {} });
      f.chars += txt;
      add(f.weights, s.fontWeight, txt);
      add(f.sizes, px(s.fontSize), txt);
      add(f.tags, tag, txt);
    }
    const bg = hex(s.backgroundColor);
    if (bg) add(R.bgColors, bg, Math.min(r.width, vw) * Math.min(r.height, 3000));
    const bw = parseFloat(s.borderTopWidth) + parseFloat(s.borderBottomWidth) + parseFloat(s.borderLeftWidth) + parseFloat(s.borderRightWidth);
    if (bw > 0 && s.borderStyle !== 'none') {
      const bc = hex(s.borderTopColor) || hex(s.borderBottomColor);
      if (bc) add(R.borderColors, bc, (r.width + r.height) * 2);
    }
    if (s.backgroundImage && s.backgroundImage !== 'none') {
      if (/gradient/.test(s.backgroundImage) && R.gradients.length < 20) R.gradients.push(s.backgroundImage.slice(0, 300));
      for (const m of s.backgroundImage.matchAll(/url\(["']?([^"')]+)["']?\)/g)) {
        const u = abs(m[1]);
        if (!u || u.startsWith('data:')) continue;
        R.bgImages.push({ url: u, dw: Math.round(r.width), dh: Math.round(r.height) });
        addLogo(el, { kind: 'bg', url: u });
      }
    }
    if (tag === 'svg' && !el.parentElement.closest('svg')) {
      const cols = {};
      for (const n of el.querySelectorAll('path, rect, circle, ellipse, polygon, polyline, line, text, use')) {
        const ns = getComputedStyle(n);
        for (const v of [ns.fill, ns.stroke]) { const h = hex(v); if (h) add(cols, h, 1); }
      }
      const cur = hex(s.color) || '#000000';
      addLogo(el, { kind: 'svg', colors: Object.keys(cols).sort((a, b) => cols[b] - cols[a]),
        svg: el.outerHTML.length < 300000 ? el.outerHTML.replace(/currentColor/gi, cur) : null });
    }
  }

  // ---------- images ----------
  const bestFromSrcset = (ss) => {
    if (!ss) return null;
    let best = null, bestW = 0;
    for (const part of ss.split(/,\s+/)) {
      const [u, d] = part.trim().split(/\s+/);
      const w = d ? parseFloat(d) * (d.endsWith('x') ? 1000 : 1) : 1;
      if (u && w >= bestW) { best = u; bestW = w; }
    }
    return best ? abs(best) : null;
  };
  for (const img of document.querySelectorAll('img')) {
    const r = img.getBoundingClientRect();
    const src = img.currentSrc || img.src || img.getAttribute('data-src') || img.getAttribute('data-lazy-src');
    const best = bestFromSrcset(img.getAttribute('srcset') || img.getAttribute('data-srcset'))
      || abs(img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original') || '');
    const info = { url: abs(src), best, alt: clean(img.alt, 200), w: img.naturalWidth, h: img.naturalHeight,
      dw: Math.round(r.width), dh: Math.round(r.height), y: Math.round(r.top + scrollY) };
    if (info.url || info.best) R.images.push(info);
    if (r.width > 0) addLogo(img, { kind: 'img', url: best || abs(src), alt: info.alt });
  }
  for (const src of document.querySelectorAll('picture source[srcset], video[poster]')) {
    const u = src.tagName === 'VIDEO' ? abs(src.getAttribute('poster')) : bestFromSrcset(src.getAttribute('srcset'));
    if (u) R.images.push({ url: u, best: u, alt: '', w: 0, h: 0, dw: 0, dh: 0, y: 0 });
  }
  logoEls.sort((a, b) => b.score - a.score);
  const bestScore = logoEls.length ? logoEls[0].score : 0;
  logoEls.filter((l) => l.score >= bestScore - 4).slice(0, 4).forEach((l, i) => {
    l.el.setAttribute('data-scrp-logo', String(i));
    const r = l.el.getBoundingClientRect();
    R.logos.push({ index: i, kind: l.kind, url: l.url || null, svg: l.svg || null, alt: l.alt || '', colors: l.colors || [],
      score: l.score, w: Math.round(r.width), h: Math.round(r.height) });
  });

  // ---------- typography & UI roles ----------
  const styleOf = (el) => {
    const s = getComputedStyle(el);
    return { fontFamily: s.fontFamily, fontSize: px(s.fontSize), fontWeight: s.fontWeight, lineHeight: /px$/.test(s.lineHeight) ? px(s.lineHeight) : s.lineHeight,
      letterSpacing: /px$/.test(s.letterSpacing) ? px(s.letterSpacing) : s.letterSpacing, textTransform: s.textTransform, color: hex(s.color),
      background: hex(s.backgroundColor), sample: clean(el.innerText, 60) };
  };
  const roleSel = { h1: 'h1', h2: 'h2', h3: 'h3', h4: 'h4', body: 'main p, article p, p', link: 'main a, p a, a',
    nav: 'nav a, header a', button: 'button, .btn, [class*=button i]', footer: 'footer' };
  for (const [role, sel] of Object.entries(roleSel)) {
    const el = [...document.querySelectorAll(sel)].find((e) => visible(e) && clean(e.innerText));
    if (el) R.roles[role] = styleOf(el);
  }
  const btnSel = 'button, input[type=submit], a[class*=btn i], a[class*=button i], [role=button], .wp-block-button__link, .elementor-button';
  for (const b of [...document.querySelectorAll(btnSel)].filter(visible).slice(0, 60)) {
    const s = getComputedStyle(b);
    const text = clean(b.innerText || b.value, 60);
    if (!text) continue;
    R.buttons.push({ text, bg: hex(s.backgroundColor), color: hex(s.color), border: hex(s.borderTopColor),
      borderWidth: s.borderTopWidth, radius: s.borderTopLeftRadius, padding: s.padding, fontWeight: s.fontWeight,
      fontSize: px(s.fontSize), textTransform: s.textTransform, fontFamily: s.fontFamily });
  }

  // ---------- content ----------
  for (const h of document.querySelectorAll('h1, h2, h3')) {
    const t = clean(h.innerText, 200);
    if (t && visible(h)) R.headings.push({ level: h.tagName.toLowerCase(), text: t });
  }
  const navRoot = document.querySelectorAll('header nav a, nav a, header a');
  const seenNav = new Set();
  for (const a of navRoot) {
    const t = clean(a.innerText, 60), h = abs(a.getAttribute('href') || '');
    if (t && h && !seenNav.has(t + h)) { seenNav.add(t + h); R.nav.push({ text: t, href: h }); }
  }
  for (const a of document.querySelectorAll('a[href]')) {
    R.links.push({ text: clean(a.innerText, 80), href: abs(a.getAttribute('href')) || a.getAttribute('href') });
  }
  const tSel = 'blockquote, [class*=testimonial i], [class*=review i], [class*=quote i]';
  const seenT = new Set();
  for (const t of document.querySelectorAll(tSel)) {
    const txt = clean(t.innerText, 500);
    if (txt.length > 30 && !seenT.has(txt) && ![...seenT].some((x) => x.includes(txt) || txt.includes(x))) {
      seenT.add(txt);
      if (R.testimonials.length < 12) R.testimonials.push(txt);
    }
  }
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { R.jsonld.push(JSON.parse(s.textContent)); } catch (e) {}
  }
  for (const m of document.querySelectorAll('meta[name], meta[property]')) {
    const k = m.getAttribute('name') || m.getAttribute('property');
    if (k && m.content) R.meta[k.toLowerCase()] = clean(m.content, 500);
  }
  R.meta.title = document.title;
  R.meta.lang = document.documentElement.lang;
  for (const l of document.querySelectorAll('link[rel*=icon i], link[rel=mask-icon], link[rel=manifest]')) {
    R.icons.push({ rel: l.getAttribute('rel'), href: abs(l.getAttribute('href')), sizes: l.getAttribute('sizes') });
  }

  // ---------- fonts & CSS variables ----------
  try {
    for (const f of document.fonts) if (f.status === 'loaded')
      R.loadedFonts.push({ family: f.family.replace(/["']/g, ''), weight: f.weight, style: f.style });
  } catch (e) {}
  for (const l of document.querySelectorAll('link[href]')) {
    const h = l.getAttribute('href');
    if (/fonts\.googleapis|fonts\.bunny|typekit|use\.fontawesome|fonts\.com|fontawesome|cloud\.typography/i.test(h)) R.fontLinks.push(abs(h));
  }
  const rootStyle = getComputedStyle(document.documentElement);
  const walk = (rules) => {
    for (const rule of rules) {
      if (rule instanceof CSSFontFaceRule) {
        if (R.fontFaces.length < 80) R.fontFaces.push({ family: rule.style.getPropertyValue('font-family').replace(/["']/g, '').trim(),
          weight: rule.style.getPropertyValue('font-weight'), src: rule.style.getPropertyValue('src').slice(0, 400) });
      } else if (rule instanceof CSSStyleRule && /(^|,)\s*(:root|html|body)\b/.test(rule.selectorText)) {
        for (const p of rule.style) if (p.startsWith('--') && Object.keys(R.vars).length < 400) {
          const v = (rootStyle.getPropertyValue(p) || rule.style.getPropertyValue(p)).trim();
          R.vars[p] = { value: v.slice(0, 200), hex: hex(v) };
        }
      } else if (rule.cssRules) walk(rule.cssRules);
    }
  };
  for (const sh of document.styleSheets) { try { walk(sh.cssRules); } catch (e) {} }

  R.text = (document.body ? document.body.innerText : '').slice(0, 60000);
  return R;
}
