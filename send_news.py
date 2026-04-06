#!/usr/bin/env python3
"""Conservation Weekly News — Searches web via Claude CLI, sends HTML digest by email."""

import json
import smtplib
import socket
import time
import sys
import os
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH   = os.path.join(SCRIPT_DIR, "config.json")
LOG_PATH      = os.path.join(SCRIPT_DIR, "sent_log.json")
HISTORY_PATH  = os.path.join(SCRIPT_DIR, "news_history.json")
PREVIEW_PATH  = os.path.join(SCRIPT_DIR, "preview.html")
WEB_PATH      = os.path.join(SCRIPT_DIR, "index.html")
CLAUDE_BIN    = "/Users/ech/.local/bin/claude"

MAX_RETRIES = 3
RETRY_DELAY = 900  # 15 min between retries

SEARCH_PROMPT = """Recherche sur le web les BONNES NOUVELLES les plus récentes en matière de conservation de la nature et de récupération des écosystèmes (7 derniers jours).
Concentre-toi exclusivement sur des histoires positives et encourageantes : populations animales en hausse, écosystèmes qui se régénèrent, espèces qui reviennent du bord de l'extinction, succès de rewilding, réserves marines florissantes, forêts qui repoussent, pollution en baisse, nouvelles zones protégées, cohabitation réussie humains-animaux sauvages.

Évite les catastrophes et crises — elles sont déjà omniprésentes. L'objectif est de contrer l'éco-anxiété en montrant ce qui FONCTIONNE.

Trouve 6-8 histoires variées en respectant impérativement cette répartition géographique :
- Au moins 2 histoires d'Europe (faune européenne, forêts, mers, politiques UE, rewilding en Europe...)
- Au moins 1 histoire d'Amériques
- Au moins 1 histoire d'Asie, Afrique, Océanie ou Arctique
- Le reste au choix

Au moins 5 articles sur les 7 derniers jours. Le fallback à 3 semaines est autorisé uniquement si aucune bonne nouvelle récente n'existe pour une région donnée — dans ce cas, indique une date précise dans le champ "date".

Privilégie ces sources fiables : Mongabay, BBC Environment, National Geographic, The Guardian Environment, WWF, IUCN, Conservation International, Nature (journal), Science (journal), NOAA Fisheries, BirdLife International. Utilise d'autres sources sérieuses si nécessaire.

Réponds UNIQUEMENT avec un tableau JSON (sans markdown, sans backticks, sans explication) dans ce format exact :
[
  {
    "title": "Titre de l'article en français",
    "summary": "Résumé en 2-3 phrases en français, mettant en valeur ce qui s'est amélioré et pourquoi c'est important",
    "category": "Faune|Océan|Forêt|Rewilding|Espèces|Politique",
    "region": "Mondial|Europe|Asie|Amériques|Afrique|Arctique|Océanie",
    "win_type": "Rebond de population|Régénération d'écosystème|Nouvelle protection|Réduction de pollution|Retour d'espèce|Rewilding",
    "emoji": "emoji animal ou nature pertinent",
    "date": "date approximative comme 'Mars 2026'",
    "url": "URL directe vers l'article source (Mongabay, BBC, etc.)",
    "impact_line": "UN chiffre-choc ou fait marquant très court (max 10 mots) qui ancre l'ampleur de la victoire. Ex: 'Population x37 en 7 ans', 'De 30 à 100 individus en 5 ans', '814 GW installés, +17% vs 2024'. Doit être concret et frappant.",
    "image_query": "2-4 mots-clés EN ANGLAIS pour recherche photo nature sur Unsplash. Décris le SUJET VISUEL concret (animal, paysage, écosystème), pas des concepts abstraits. Inclus l'animal ou l'habitat principal + un contexte géographique ou visuel. BON: 'european bison snowy forest', 'coral reef colorful fish underwater', 'amazon canopy aerial green'. MAUVAIS: 'conservation policy', 'biodiversity protection', 'renewable energy chart'."
  }
]"""

WIN_COLOR = {
    # French keys (SEARCH_PROMPT)
    "Rebond de population":        "#2d6a4f",
    "Régénération d'écosystème":   "#457b9d",
    "Nouvelle protection":         "#024873",
    "Réduction de pollution":      "#1d7a8a",
    "Retour d'espèce":             "#606c38",
    "Rewilding":                   "#8b5e3c",
    # English keys (news_data.json)
    "species_recovery":            "#2d6a4f",
    "habitat_protection":          "#457b9d",
    "renewable_energy":            "#c4631a",
    "policy_win":                  "#024873",
    "scientific_discovery":        "#5c4b8a",
    "community_action":            "#606c38",
}
WIN_DEFAULT_COLOR = "#024873"


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def fetch_news():
    """Invoke Claude CLI with WebSearch to get conservation news as JSON.
    Works when called outside a Claude Code session (e.g. via launchd).
    """
    print("Fetching news via Claude...")
    # Strip CLAUDECODE so nested-session guard doesn't trigger
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        [CLAUDE_BIN, "-p", SEARCH_PROMPT,
         "--allowed-tools", "WebSearch",
         "--output-format", "text",
         "--no-session-persistence"],
        capture_output=True, text=True, timeout=180, env=env
    )
    if result.returncode != 0:
        raise RuntimeError(f"Claude exited {result.returncode}: {result.stderr[:400]}")

    raw = result.stdout.strip()
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("[")
    end   = cleaned.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(f"No JSON array in Claude output:\n{raw[:500]}")

    return json.loads(cleaned[start:end])


def fetch_images(news, access_key):
    """Fetch a landscape photo from Unsplash for each article using image_query."""
    if not access_key:
        print("No Unsplash key — skipping images.")
        return
    CATEGORY_COLOR = {
        "forest": "green", "Forêt": "green",
        "ocean": "blue", "Océan": "blue",
        "wildlife": None, "Faune": None,
        "climate": None, "Politique": None,
        "biodiversity": None, "Espèces": None,
        "Rewilding": "green",
    }
    base = "https://api.unsplash.com/search/photos"
    for item in news:
        query = item.get("image_query", "")
        if not query:
            continue
        try:
            params_dict = {
                "query": query,
                "orientation": "landscape",
                "per_page": 1,
                "content_filter": "high",
            }
            color = CATEGORY_COLOR.get(item.get("category"))
            if color:
                params_dict["color"] = color
            params = urllib.parse.urlencode(params_dict)
            req = urllib.request.Request(
                f"{base}?{params}",
                headers={"Authorization": f"Client-ID {access_key}",
                         "Accept-Version": "v1"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if data.get("results"):
                photo = data["results"][0]
                item["image_url"] = photo["urls"]["small"]          # 400px — grid cards
                item["image_url_large"] = photo["urls"]["regular"]  # 1080px — featured
                item["image_credit"] = photo["user"]["name"]
                item["image_credit_url"] = photo["user"]["links"]["html"]
        except Exception as e:
            print(f"  Image fetch failed for '{query}': {e}")


def build_email_html(news):
    date_str = datetime.now().strftime("%A %d %B %Y")
    cards_html = ""
    for item in news:
        win   = item.get("win_type", "species_recovery")
        color = WIN_COLOR.get(win, WIN_DEFAULT_COLOR)
        source = item.get("source_url") or item.get("url", "")
        source_html = (
            f'<a href="{source}" style="color:{color}; text-decoration:none; font-size:11px; '
            f'letter-spacing:1px; text-transform:uppercase; font-family:Helvetica Neue,Arial,sans-serif;">'
            f'Lire l\'article ↗</a>'
        ) if source else ""
        cards_html += f"""
        <tr>
          <td style="padding: 0 24px 20px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#ffffff; border:1px solid #e0ddd4; border-top:3px solid {color};">
              <tr>
                <td style="padding:20px 22px;">
                  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
                    <tr>
                      <td>
                        <span style="font-size:10px; letter-spacing:2px; text-transform:uppercase; color:{color};
                              font-family:Helvetica Neue,Arial,sans-serif; font-weight:600;">{item.get("category","").upper()}</span>
                        <span style="font-size:10px; color:#ccc; font-family:Helvetica Neue,Arial,sans-serif;"> · </span>
                        <span style="font-size:10px; letter-spacing:1.5px; text-transform:uppercase; color:#999;
                              font-family:Helvetica Neue,Arial,sans-serif;">{item.get("region","").upper()}</span>
                      </td>
                      <td align="right" style="font-size:20px;">{item.get("emoji","🌿")}</td>
                    </tr>
                  </table>
                  <div style="font-size:16px; font-weight:700; color:#1a1a1a; line-height:1.35; margin-bottom:10px;
                              font-family:Georgia,'Times New Roman',serif; letter-spacing:-0.2px;">
                    {item.get("title","")}
                  </div>
                  <div style="font-size:13px; color:#555; line-height:1.65; margin-bottom:16px;
                              font-family:Helvetica Neue,Arial,sans-serif;">
                    {item.get("summary","")}
                  </div>
                  <table width="100%" cellpadding="0" cellspacing="0"
                         style="border-top:1px solid #ece9e0;">
                    <tr>
                      <td style="font-size:11px; color:#bbb; font-family:Helvetica Neue,Arial,sans-serif;
                                 padding-top:12px;">{item.get("date","")}</td>
                      <td align="right" style="padding-top:12px;">{source_html}</td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0; padding:0; background:#f5f4ee; font-family:Georgia,'Times New Roman',serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f4ee; padding:32px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0"
             style="background:#fdfcf3; border:1px solid #d8d4c4;">
        <tr>
          <td style="padding:44px 32px 32px; border-bottom:1px solid #d8d4c4; text-align:center;">
            <div style="font-size:36px; font-weight:700; color:#1a1a1a; letter-spacing:-1.5px;
                        font-family:Georgia,'Times New Roman',serif;">La Nature Résiste</div>
            <div style="font-size:11px; color:#999; margin-top:12px; letter-spacing:1px; text-transform:uppercase;
                        font-family:Helvetica Neue,Arial,sans-serif;">Semaine du {date_str}</div>
          </td>
        </tr>
        <tr><td style="padding:20px 0 4px;"></td></tr>
        {cards_html}
        <tr>
          <td style="padding:20px 24px 28px; border-top:1px solid #d8d4c4; text-align:center;">
            <div style="font-size:10px; color:#bbb; letter-spacing:2px; text-transform:uppercase;
                        font-family:Helvetica Neue,Arial,sans-serif;">
              {len(news)} victoires pour la nature cette semaine
            </div>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_email(config, subject, html_body):
    msg = MIMEMultipart("alternative")
    msg["From"]    = config["sender_email"]
    msg["To"]      = config["recipient_email"]
    msg["Subject"] = subject
    msg.attach(MIMEText("Conservation news digest — open in HTML for best experience.", "plain"))
    msg.attach(MIMEText(html_body, "html"))

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with smtplib.SMTP(config["smtp_server"], config["smtp_port"]) as server:
                server.starttls()
                server.login(config["sender_email"], config["app_password"])
                server.send_message(msg)
            print(f"Email sent to {config['recipient_email']}")
            return
        except (socket.gaierror, smtplib.SMTPConnectError, OSError) as e:
            if attempt < MAX_RETRIES:
                print(f"Attempt {attempt}/{MAX_RETRIES} failed ({e}). Retrying in 15 min...")
                time.sleep(RETRY_DELAY)
            else:
                raise


def run(test_mode=False, mock_data=None):
    config = load_json(CONFIG_PATH)
    if not config.get("sender_email") or not config.get("app_password"):
        print("Config missing. Edit config.json with Gmail credentials.")
        sys.exit(1)

    news = mock_data if mock_data else fetch_news()
    if not news:
        print("No news returned.")
        return

    unsplash_key = config.get("unsplash_access_key", "")
    if unsplash_key and not mock_data:
        fetch_images(news, unsplash_key)

    date_str = datetime.now().strftime("%d %B %Y")
    subject  = f"🌿 La Nature Résiste — {date_str}"
    html     = build_email_html(news)

    if test_mode:
        with open(PREVIEW_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        web_html = build_web_page(news)
        with open(WEB_PATH, "w", encoding="utf-8") as f:
            f.write(web_html)
        print(f"\n{len(news)} articles:")
        for i, item in enumerate(news, 1):
            has_img = "📷" if item.get("image_url") else "  "
            print(f"  {i}. {has_img} [{item.get('win_type','?'):<24}] [{item.get('category')}] {item.get('title')}")
        print(f"\nEmail preview → {PREVIEW_PATH}")
        print(f"Web preview   → {WEB_PATH}")
        return

    send_email(config, subject, html)
    publish_to_github(news)

    log = load_json(LOG_PATH, default={"sent": []})
    log["sent"].append({
        "date": datetime.now().isoformat(),
        "count": len(news),
        "titles": [n.get("title") for n in news]
    })
    save_json(LOG_PATH, log)
    print(f"Logged. Total digests sent: {len(log['sent'])}")


def build_web_page(news, history=None):
    date_str = datetime.now().strftime("%A %d %B %Y")
    updated  = datetime.now().strftime("%d/%m/%Y à %H:%M")
    if history is None:
        history = []

    # Stats — cumulative hope counter across all editions
    edition_num  = len(history)
    total_victories = sum(len(issue.get("articles", [])) for issue in history)
    first_date = history[-1].get("label", "") if history else ""
    species_back = sum(1 for a in news if a.get("win_type") in [
        "species_recovery", "Retour d'espèce", "Rebond de population"])
    policy_wins  = sum(1 for a in news if a.get("win_type") in [
        "policy_win", "Nouvelle protection", "habitat_protection"])
    n_regions    = len(set(
        a.get("region", "") for a in news
        if a.get("region") and a.get("region") not in ("Monde", "")
    ))

    stats_parts = [f"Édition n°{edition_num}"]
    if total_victories and first_date:
        stats_parts.append(f"{total_victories} victoires pour la nature depuis le {first_date}")
    else:
        stats_parts.append(f"{len(news)} victoires cette semaine")
    stats_parts.append(f"{n_regions} régions couvertes")
    stats_html = ' <span class="stats-sep">·</span> '.join(
        f'<span>{p}</span>' for p in stats_parts)

    # Win type labels for filter pills
    WIN_TYPE_LABELS = {
        "species_recovery": "Retour d'espèces",
        "Retour d'espèce": "Retour d'espèces",
        "Rebond de population": "Retour d'espèces",
        "policy_win": "Politiques",
        "Nouvelle protection": "Politiques",
        "habitat_protection": "Protection d'habitats",
        "Régénération d'écosystème": "Protection d'habitats",
        "renewable_energy": "Énergies propres",
        "scientific_discovery": "Science",
        "community_action": "Action locale",
        "Rewilding": "Rewilding",
    }
    # Collect unique win_type labels present in this edition
    win_types_present = {}
    for a in news:
        wt = a.get("win_type", "")
        label = WIN_TYPE_LABELS.get(wt, wt)
        if label and label not in win_types_present:
            win_types_present[label] = wt
    impact_pills_html = ""
    for label in win_types_present:
        slug = label.lower().replace(" ", "-").replace("'", "")
        impact_pills_html += f'<button class="pill" data-impact="{slug}">{label}</button>\n      '


    # Featured article (first) + grid articles (rest)
    featured  = news[0] if news else None
    grid_news = news[1:] if len(news) > 1 else []

    featured_html = ""
    if featured:
        fw   = featured.get("win_type", "species_recovery")
        fc   = WIN_COLOR.get(fw, WIN_DEFAULT_COLOR)
        fsrc = featured.get("source_url") or featured.get("url", "")
        fsrc_html = (
            f'<a class="source-link" href="{fsrc}" target="_blank" rel="noopener">Lire l\'article ↗</a>'
        ) if fsrc else ""
        fshare_url = fsrc or "https://emilechevalier.github.io/news-nature/"
        fshare_html = (
            f'<button class="share-btn" data-url="{fshare_url}" '
            f'data-title="{featured.get("title","")}" aria-label="Partager">Partager ↗</button>'
        )
        fimg = featured.get("image_url_large", "") or featured.get("image_url", "")
        fimg_html = f'<div class="featured-image" style="background-image:url({fimg})"></div>' if fimg else ""
        fcredit = featured.get("image_credit", "")
        fcredit_url = featured.get("image_credit_url", "")
        fcredit_html = (
            f'<div class="image-credit">Photo: <a href="{fcredit_url}?utm_source=la_nature_resiste&utm_medium=referral" '
            f'target="_blank" rel="noopener">{fcredit}</a> / Unsplash</div>'
        ) if fcredit else ""
        fimpact = featured.get("impact_line", "")
        fimpact_html = f'<div class="impact-line">{fimpact}</div>' if fimpact else ""
        fwt_label = WIN_TYPE_LABELS.get(fw, fw)
        fwt_slug = fwt_label.lower().replace(" ", "-").replace("'", "")
        featured_html = f"""
  <div class="featured-wrapper" data-region="{featured.get('region','')}" data-wintype="{fwt_slug}">
    <div class="featured-label">✦ Article phare de la semaine</div>
    <div class="featured-card" style="border-top:4px solid {fc}">
      {fimg_html}
      <div class="featured-content">
        <div class="card-tags" style="margin-bottom:12px">
          <span class="tag-cat" style="color:{fc}">{featured.get("category","").upper()}</span>
          <span class="tag-sep">·</span>
          <span class="tag-reg">{featured.get("region","").upper()}</span>
        </div>
        <h2 class="featured-title">{featured.get("title","")}</h2>
        {fimpact_html}
        <p class="featured-summary">{featured.get("summary","")}</p>
        <div class="card-footer">
          <span class="card-date">{featured.get("date","")}</span>
          <div class="card-actions">
            {fshare_html}
            {fsrc_html}
          </div>
        </div>
        {fcredit_html}
      </div>
    </div>
  </div>"""

    # Grid cards (remaining articles)
    cards_html = ""
    for item in grid_news:
        win    = item.get("win_type", "species_recovery")
        color  = WIN_COLOR.get(win, WIN_DEFAULT_COLOR)
        source = item.get("source_url") or item.get("url", "")
        source_html = (
            f'<a class="source-link" href="{source}" target="_blank" rel="noopener">Lire ↗</a>'
        ) if source else ""
        share_url = source or "https://emilechevalier.github.io/news-nature/"
        share_html = (
            f'<button class="share-btn" data-url="{share_url}" '
            f'data-title="{item.get("title","")}" aria-label="Partager">Partager</button>'
        )
        img_url = item.get("image_url", "")
        img_html = f'<div class="card-image" style="background-image:url({img_url})"></div>' if img_url else ""
        credit = item.get("image_credit", "")
        credit_url = item.get("image_credit_url", "")
        credit_html = (
            f'<div class="image-credit">Photo: <a href="{credit_url}?utm_source=la_nature_resiste&utm_medium=referral" '
            f'target="_blank" rel="noopener">{credit}</a> / Unsplash</div>'
        ) if credit else ""
        impact = item.get("impact_line", "")
        impact_html = f'<div class="impact-line">{impact}</div>' if impact else ""
        wt_label = WIN_TYPE_LABELS.get(win, win)
        wt_slug = wt_label.lower().replace(" ", "-").replace("'", "")
        cards_html += f"""
        <div class="card" data-region="{item.get('region','')}" data-wintype="{wt_slug}" style="border-top:3px solid {color}">
          {img_html}
          <div class="card-body">
            <div class="card-meta">
              <div class="card-tags">
                <span class="tag-cat" style="color:{color}">{item.get("category","").upper()}</span>
                <span class="tag-sep">·</span>
                <span class="tag-reg">{item.get("region","").upper()}</span>
              </div>
              <span class="card-emoji">{item.get("emoji","🌿")}</span>
            </div>
            <h2 class="card-title">{item.get("title","")}</h2>
            {impact_html}
            <p class="card-summary">{item.get("summary","")}</p>
            <div class="card-footer">
              <span class="card-date">{item.get("date","")}</span>
              <div class="card-actions">
                {share_html}
                {source_html}
              </div>
            </div>
            {credit_html}
          </div>
        </div>"""

    # History HTML — compact list grouped by issue
    history_html = ""
    for issue in history:
        rows = ""
        for a in issue.get("articles", []):
            src = a.get("source_url") or a.get("url", "")
            src_html = f'<a class="hist-link" href="{src}" target="_blank" rel="noopener">↗</a>' if src else ""
            rows += f"""
          <li class="hist-item">
            <span class="hist-emoji">{a.get("emoji","🌿")}</span>
            <span class="hist-title">{a.get("title","")}</span>
            {src_html}
          </li>"""
        history_html += f"""
        <div class="hist-issue">
          <div class="hist-issue-date">{issue.get("label","")}</div>
          <ul class="hist-list">{rows}
          </ul>
        </div>"""
    if not history_html:
        history_html = '<p class="hist-empty">L\'historique s\'enrichira à chaque nouvelle édition.</p>'

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>La Nature Résiste</title>
  <meta name="description" content="Les bonnes nouvelles de la nature cette semaine — espèces qui reviennent, écosystèmes qui se régénèrent.">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      background: #f5f4ee;
      color: #1a1a1a;
      font-family: Georgia, 'Times New Roman', serif;
      min-height: 100vh;
    }}

    header {{
      background: #fdfcf3;
      border-bottom: 1px solid #d8d4c4;
      padding: 52px 24px 36px;
      text-align: center;
    }}

    .header-label {{
      font-size: 10px;
      letter-spacing: 4px;
      color: #aaa;
      text-transform: uppercase;
      margin-bottom: 18px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    h1 {{
      font-size: clamp(32px, 6vw, 56px);
      font-weight: 700;
      color: #1a1a1a;
      letter-spacing: -2px;
      line-height: 1.05;
      margin-bottom: 14px;
    }}

    .header-date {{
      font-size: 11px;
      color: #aaa;
      letter-spacing: 2px;
      text-transform: uppercase;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    /* Stats bar */
    .stats-bar {{
      background: #fdfcf3;
      border-bottom: 1px solid #d8d4c4;
      padding: 10px 20px;
      display: flex;
      justify-content: center;
      flex-wrap: wrap;
      gap: 6px;
      font-size: 10px;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: #999;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    .stats-sep {{ color: #d8d4c4; }}

    /* Tabs + filters */
    .tabs-wrapper {{
      background: #fdfcf3;
      border-bottom: 1px solid #d8d4c4;
    }}

    .tabs {{
      display: flex;
      max-width: 1100px;
      margin: 0 auto;
      padding: 0 20px;
    }}

    .tab {{
      padding: 14px 28px;
      border: none;
      border-bottom: 2px solid transparent;
      background: transparent;
      color: #aaa;
      font-size: 11px;
      letter-spacing: 2px;
      text-transform: uppercase;
      cursor: pointer;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      transition: color .15s, border-color .15s;
      margin-bottom: -1px;
    }}

    .tab:hover {{ color: #1a1a1a; }}

    .tab.active {{
      color: #1a1a1a;
      border-bottom-color: #024873;
    }}

    .tab-history {{
      margin-left: auto;
      border-left: 1px solid #d8d4c4;
    }}

    /* Impact filter pills */
    .filters-bar {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 14px 20px 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }}

    .filters-label {{
      font-size: 10px;
      letter-spacing: 2px;
      text-transform: uppercase;
      color: #aaa;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      margin-right: 4px;
    }}

    .pill {{
      padding: 5px 14px;
      border: 1px solid #d8d4c4;
      border-radius: 20px;
      background: transparent;
      color: #888;
      font-size: 11px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      cursor: pointer;
      transition: all .15s;
    }}

    .pill:hover {{ border-color: #999; color: #555; }}

    .pill.active {{
      background: #024873;
      color: #fff;
      border-color: #024873;
    }}

    /* Featured article */
    .featured-wrapper {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 32px 20px 0;
    }}

    .featured-label {{
      font-size: 10px;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: #aaa;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      margin-bottom: 12px;
    }}

    .featured-card {{
      background: #ffffff;
      border: 1px solid #e0ddd4;
      overflow: hidden;
    }}

    .featured-image {{
      width: 100%;
      height: 280px;
      background-size: cover;
      background-position: center;
      background-color: #e8e6de;
    }}

    .featured-content {{ padding: 32px; }}

    .featured-title {{
      font-size: clamp(18px, 2.5vw, 24px);
      font-weight: 700;
      color: #1a1a1a;
      line-height: 1.25;
      letter-spacing: -0.5px;
      margin-bottom: 14px;
    }}

    .featured-summary {{
      font-size: 14px;
      color: #444;
      line-height: 1.72;
      margin-bottom: 20px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    @media (max-width: 600px) {{
      .featured-image {{ height: 200px; }}
      .featured-content {{ padding: 20px; }}
    }}

    /* Grid */
    #grid {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 24px 20px 60px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }}

    .card {{
      background: #ffffff;
      border: 1px solid #e0ddd4;
      overflow: hidden;
      transition: box-shadow .2s, transform .2s;
    }}

    .card:hover {{
      box-shadow: 0 6px 28px rgba(0,0,0,0.08);
      transform: translateY(-2px);
    }}

    .card.hidden {{ display: none; }}

    .card-image {{
      width: 100%;
      height: 180px;
      background-size: cover;
      background-position: center;
      background-color: #e8e6de;
    }}

    .card-body {{ padding: 24px; }}

    .card-meta {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 14px;
    }}

    .card-tags {{
      display: flex;
      align-items: center;
      gap: 7px;
    }}

    .tag-cat {{
      font-size: 10px;
      letter-spacing: 2px;
      text-transform: uppercase;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-weight: 600;
    }}

    .tag-sep {{
      font-size: 10px;
      color: #ccc;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    .tag-reg {{
      font-size: 10px;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color: #999;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    .card-emoji {{ font-size: 20px; line-height: 1; }}

    .card-title {{
      font-size: 16px;
      font-weight: 700;
      color: #1a1a1a;
      line-height: 1.35;
      margin-bottom: 12px;
      letter-spacing: -0.3px;
    }}

    .card-summary {{
      font-size: 13px;
      color: #555;
      line-height: 1.68;
      margin-bottom: 18px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    /* Impact line — chiffre-choc */
    .impact-line {{
      font-size: 13px;
      font-weight: 700;
      color: #2d6a4f;
      background: #eef6f0;
      border-left: 3px solid #2d6a4f;
      padding: 6px 12px;
      margin-bottom: 14px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      letter-spacing: 0.3px;
    }}

    /* Share button */
    .share-btn {{
      background: none;
      border: 1px solid #d8d4c4;
      color: #888;
      font-size: 11px;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      letter-spacing: 1px;
      padding: 3px 10px;
      border-radius: 3px;
      cursor: pointer;
      transition: all .15s;
    }}

    .share-btn:hover {{ border-color: #024873; color: #024873; }}

    .share-btn.copied {{
      border-color: #2d6a4f;
      color: #2d6a4f;
    }}

    .card-actions {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}

    .card-footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-top: 14px;
      border-top: 1px solid #ece9e0;
    }}

    .card-date {{
      font-size: 11px;
      color: #bbb;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      letter-spacing: 0.5px;
    }}

    .source-link {{
      font-size: 11px;
      color: #024873;
      text-decoration: none;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      letter-spacing: 1px;
      text-transform: uppercase;
    }}

    .source-link:hover {{ text-decoration: underline; }}

    .image-credit {{
      font-size: 10px;
      color: #bbb;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      margin-top: 8px;
    }}

    .image-credit a {{
      color: #bbb;
      text-decoration: none;
    }}

    .image-credit a:hover {{ text-decoration: underline; }}

    .empty-msg {{
      grid-column: 1/-1;
      text-align: center;
      padding: 60px 20px;
      color: #bbb;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 11px;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}

    /* History view */
    #history-view {{
      display: none;
      max-width: 760px;
      margin: 0 auto;
      padding: 40px 20px 60px;
    }}

    .hist-issue {{ margin-bottom: 40px; }}

    .hist-issue-date {{
      font-size: 10px;
      letter-spacing: 3px;
      text-transform: uppercase;
      color: #aaa;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      padding-bottom: 12px;
      border-bottom: 1px solid #d8d4c4;
      margin-bottom: 4px;
    }}

    .hist-list {{ list-style: none; }}

    .hist-item {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid #ece9e0;
    }}

    .hist-emoji {{ font-size: 14px; flex-shrink: 0; line-height: 1.5; }}

    .hist-title {{
      flex: 1;
      font-size: 14px;
      color: #1a1a1a;
      line-height: 1.4;
      font-family: Georgia, 'Times New Roman', serif;
    }}

    .hist-link {{
      font-size: 11px;
      color: #024873;
      text-decoration: none;
      flex-shrink: 0;
      opacity: 0.6;
    }}

    .hist-link:hover {{ opacity: 1; }}

    .hist-empty {{
      text-align: center;
      padding: 60px 20px;
      color: #bbb;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      font-size: 12px;
      letter-spacing: 1px;
    }}

    footer {{
      text-align: center;
      padding: 28px 24px;
      font-size: 10px;
      color: #bbb;
      border-top: 1px solid #d8d4c4;
      background: #fdfcf3;
      font-family: 'Helvetica Neue', Arial, sans-serif;
      letter-spacing: 2px;
      text-transform: uppercase;
    }}
  </style>
</head>
<body>
  <header>
    <h1>La Nature Résiste</h1>
    <div class="header-date">Semaine du {date_str}</div>
  </header>

  <div class="stats-bar">
    {stats_html}
  </div>

  <div class="tabs-wrapper">
    <div class="tabs">
      <button class="tab active" data-filter="all">Tout</button>
      <button class="tab" data-filter="europe">Europe</button>
      <button class="tab" data-filter="world">Reste du monde</button>
      <button class="tab tab-history" data-filter="history">Historique</button>
    </div>
  </div>

  <div class="filters-bar" id="filters-bar">
    <span class="filters-label">Impact :</span>
    <button class="pill active" data-impact="all">Tous</button>
    {impact_pills_html}
  </div>

  {featured_html}

  <div id="grid">
    {cards_html}
    <div class="empty-msg" id="empty-msg" style="display:none">Aucune actualité pour ce filtre.</div>
  </div>

  <div id="history-view">
    {history_html}
  </div>

  <footer>
    Mis à jour le {updated}
  </footer>

  <script>
    const tabs       = document.querySelectorAll('.tab');
    const pills      = document.querySelectorAll('.pill');
    const cards      = document.querySelectorAll('.card');
    const emptyMsg   = document.getElementById('empty-msg');
    const grid       = document.getElementById('grid');
    const featWrap   = document.querySelector('.featured-wrapper');
    const histView   = document.getElementById('history-view');
    const filtersBar = document.getElementById('filters-bar');

    let activeRegion = 'all';
    let activeImpact = 'all';

    function applyFilters() {{
      // Featured
      if (featWrap) {{
        const fRegion  = featWrap.dataset.region || '';
        const fWintype = featWrap.dataset.wintype || '';
        const fIsEurope = fRegion === 'Europe';
        const regionOk = activeRegion === 'all'
          || (activeRegion === 'europe' && fIsEurope)
          || (activeRegion === 'world'  && !fIsEurope);
        const impactOk = activeImpact === 'all' || fWintype === activeImpact;
        featWrap.style.display = (regionOk && impactOk) ? '' : 'none';
      }}

      let visible = 0;
      cards.forEach(card => {{
        const region   = card.dataset.region || '';
        const wintype  = card.dataset.wintype || '';
        const isEurope = region === 'Europe';
        const regionOk = activeRegion === 'all'
          || (activeRegion === 'europe' && isEurope)
          || (activeRegion === 'world'  && !isEurope);
        const impactOk = activeImpact === 'all' || wintype === activeImpact;
        const show = regionOk && impactOk;
        card.classList.toggle('hidden', !show);
        if (show) visible++;
      }});

      emptyMsg.style.display = visible === 0 ? 'block' : 'none';
    }}

    tabs.forEach(tab => {{
      tab.addEventListener('click', () => {{
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        const filter = tab.dataset.filter;

        if (filter === 'history') {{
          if (featWrap) featWrap.style.display = 'none';
          grid.style.display = 'none';
          filtersBar.style.display = 'none';
          histView.style.display = 'block';
          return;
        }}

        grid.style.display = 'grid';
        filtersBar.style.display = 'flex';
        histView.style.display = 'none';
        activeRegion = filter;
        applyFilters();
      }});
    }});

    pills.forEach(pill => {{
      pill.addEventListener('click', () => {{
        pills.forEach(p => p.classList.remove('active'));
        pill.classList.add('active');
        activeImpact = pill.dataset.impact;
        applyFilters();
      }});
    }});

    // Share buttons — Web Share API with clipboard fallback
    document.querySelectorAll('.share-btn').forEach(btn => {{
      btn.addEventListener('click', async () => {{
        const url   = btn.dataset.url;
        const title = btn.dataset.title;
        const text  = title + ' — La Nature Résiste';
        if (navigator.share) {{
          try {{
            await navigator.share({{ title: text, url: url }});
          }} catch (e) {{ /* user cancelled */ }}
        }} else {{
          try {{
            await navigator.clipboard.writeText(url);
            btn.textContent = 'Copié !';
            btn.classList.add('copied');
            setTimeout(() => {{
              btn.textContent = btn.closest('.featured-content') ? 'Partager ↗' : 'Partager';
              btn.classList.remove('copied');
            }}, 2000);
          }} catch (e) {{ /* fallback: do nothing */ }}
        }}
      }});
    }});
  </script>
</body>
</html>"""


def append_to_history(news):
    """Append current issue to news_history.json."""
    history = load_json(HISTORY_PATH, default=[])
    today = datetime.now().strftime("%Y-%m-%d")
    label = datetime.now().strftime("%-d %B %Y")
    # Avoid duplicate entries for the same day
    if not history or history[0].get("date") != today:
        history.insert(0, {"date": today, "label": label, "articles": news})
        save_json(HISTORY_PATH, history)


def publish_to_github(news):
    """Generate index.html and push to GitHub Pages."""
    append_to_history(news)
    history = load_json(HISTORY_PATH, default=[])
    html = build_web_page(news, history)

    with open(WEB_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    save_json(os.path.join(SCRIPT_DIR, "news_data.json"), news)

    date_str = datetime.now().strftime("%d %B %Y")
    env = {k: v for k, v in os.environ.items()}
    cmds = [
        ["git", "add", "index.html", "news_data.json", "news_history.json"],
        ["git", "commit", "-m", f"news: {date_str}"],
        ["git", "push", "origin", "main"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR, env=env)
        if r.returncode != 0:
            raise RuntimeError(f"`{' '.join(cmd)}` failed: {r.stderr[:300]}")
    print("Page published → https://emilechevalier.github.io/news-nature/")


def main():
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    if arg == "--test-email":
        # Test email pipeline with sample data (no Claude call)
        config = load_json(CONFIG_PATH)
        sample = [
            {"title": "Test: Coral Reef Recovery in Pacific", "summary": "Scientists report unprecedented recovery of coral reefs across the Western Pacific, with coverage increasing 25% since 2023.",
             "category": "Ocean", "region": "Asia", "win_type": "Ecosystem recovery", "emoji": "🐠", "date": "March 2026",
             "image_query": "coral reef tropical fish"},
            {"title": "Test: Wolves Return to French Alps", "summary": "Wolf population reaches 200 individuals in the French Alps, marking a historic milestone for European rewilding efforts.",
             "category": "Wildlife", "region": "Europe", "win_type": "Species return", "emoji": "🐺", "date": "March 2026",
             "image_query": "grey wolf forest"},
        ]
        # Fetch images for test data too
        unsplash_key = config.get("unsplash_access_key", "")
        if unsplash_key:
            fetch_images(sample, unsplash_key)
        run(test_mode=True, mock_data=sample)
    elif arg == "--test":
        run(test_mode=True)
    elif arg in ("", "--send"):
        run(test_mode=False)
    else:
        print("Usage:")
        print("  python send_news.py              Send the weekly digest (fetch + email)")
        print("  python send_news.py --test       Fetch news, preview HTML (no email)")
        print("  python send_news.py --test-email Test email layout only (no Claude, no send)")


if __name__ == "__main__":
    main()
