#!/usr/bin/env python3
"""Conservation Weekly News — Searches web via Claude CLI, sends HTML digest by email."""

import json
import smtplib
import socket
import time
import sys
import os
import subprocess
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
    "url": "URL directe vers l'article source (Mongabay, BBC, etc.)"
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
            <div style="font-size:10px; letter-spacing:4px; color:#aaa; text-transform:uppercase;
                        margin-bottom:16px; font-family:Helvetica Neue,Arial,sans-serif;">
              Bonnes nouvelles de la nature
            </div>
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

    date_str = datetime.now().strftime("%d %B %Y")
    subject  = f"🌿 La Nature Résiste — {date_str}"
    html     = build_email_html(news)

    if test_mode:
        with open(PREVIEW_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n{len(news)} articles:")
        for i, item in enumerate(news, 1):
            print(f"  {i}. [{item.get('win_type','?'):<24}] [{item.get('category')}] {item.get('title')}")
        print(f"\nHTML preview → {PREVIEW_PATH}")
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

    cards_html = ""
    for item in news:
        win   = item.get("win_type", "species_recovery")
        color = WIN_COLOR.get(win, WIN_DEFAULT_COLOR)
        source = item.get("source_url") or item.get("url", "")
        source_html = (
            f'<a class="source-link" href="{source}" target="_blank" rel="noopener">Lire l\'article ↗</a>'
        ) if source else ""
        cards_html += f"""
        <div class="card" data-region="{item.get('region','')}" style="border-top:3px solid {color}">
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
            <p class="card-summary">{item.get("summary","")}</p>
            <div class="card-footer">
              <span class="card-date">{item.get("date","")}</span>
              {source_html}
            </div>
          </div>
        </div>"""

    # Build history HTML — compact list grouped by issue
    history_html = ""
    for issue in history:
        issue_articles = issue.get("articles", [])
        rows = ""
        for a in issue_articles:
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
      padding: 52px 24px 40px;
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
      margin-bottom: 16px;
    }}

    .header-date {{
      font-size: 11px;
      color: #aaa;
      letter-spacing: 2px;
      text-transform: uppercase;
      font-family: 'Helvetica Neue', Arial, sans-serif;
    }}

    /* Tabs */
    .tabs-wrapper {{
      background: #fdfcf3;
      border-bottom: 1px solid #d8d4c4;
    }}

    .tabs {{
      display: flex;
      justify-content: center;
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

    /* Grid */
    #grid {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 36px 20px 60px;
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

    .hist-issue {{
      margin-bottom: 40px;
    }}

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

    .hist-list {{
      list-style: none;
    }}

    .hist-item {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px solid #ece9e0;
    }}

    .hist-emoji {{
      font-size: 14px;
      flex-shrink: 0;
      line-height: 1.5;
    }}

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
      letter-spacing: 0.5px;
      opacity: 0.7;
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

    /* History tab — aligned right */
    .tab-history {{
      margin-left: auto;
      border-left: 1px solid #d8d4c4;
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
    <div class="header-label">Agent IA · Conservation de la nature</div>
    <h1>La Nature Résiste</h1>
    <div class="header-date">Semaine du {date_str}</div>
  </header>

  <div class="tabs-wrapper">
    <div class="tabs">
      <button class="tab active" data-filter="all">Toutes les régions</button>
      <button class="tab" data-filter="europe">Europe</button>
      <button class="tab" data-filter="world">Reste du monde</button>
      <button class="tab tab-history" data-filter="history">Historique</button>
    </div>
  </div>

  <div id="grid">
    {cards_html}
    <div class="empty-msg" id="empty-msg" style="display:none">Aucune actualité pour ce filtre.</div>
  </div>

  <div id="history-view">
    {history_html}
  </div>

  <footer>
    {len(news)} victoires pour la nature · Mis à jour le {updated}
  </footer>

  <script>
    const tabs = document.querySelectorAll('.tab');
    const cards = document.querySelectorAll('.card');
    const emptyMsg = document.getElementById('empty-msg');
    const grid = document.getElementById('grid');
    const historyView = document.getElementById('history-view');

    tabs.forEach(tab => {{
      tab.addEventListener('click', () => {{
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        const filter = tab.dataset.filter;

        if (filter === 'history') {{
          grid.style.display = 'none';
          historyView.style.display = 'block';
          return;
        }}

        grid.style.display = 'grid';
        historyView.style.display = 'none';

        let visible = 0;
        cards.forEach(card => {{
          const region = card.dataset.region || '';
          const isEurope = region === 'Europe';
          const show = filter === 'all'
            || (filter === 'europe' && isEurope)
            || (filter === 'world'  && !isEurope);

          card.classList.toggle('hidden', !show);
          if (show) visible++;
        }});

        emptyMsg.style.display = visible === 0 ? 'block' : 'none';
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

    date_str = datetime.now().strftime("%d %B %Y")
    env = {k: v for k, v in os.environ.items()}
    cmds = [
        ["git", "add", "index.html", "news_history.json"],
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
        sample = [
            {"title": "Test: Coral Reef Recovery in Pacific", "summary": "Scientists report unprecedented recovery...",
             "category": "Ocean", "region": "Asia", "win_type": "Ecosystem recovery", "emoji": "🐠", "date": "March 2026"},
            {"title": "Test: Wolves Return to French Alps", "summary": "Wolf population reaches 200 individuals...",
             "category": "Wildlife", "region": "Europe", "win_type": "Species return", "emoji": "🐺", "date": "March 2026"},
        ]
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
