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

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPT_DIR, "config.json")
LOG_PATH     = os.path.join(SCRIPT_DIR, "sent_log.json")
PREVIEW_PATH = os.path.join(SCRIPT_DIR, "preview.html")
WEB_PATH     = os.path.join(SCRIPT_DIR, "index.html")
CLAUDE_BIN   = "/Users/ech/.local/bin/claude"

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
    "Rebond de population":        "#2eb82e",
    "Régénération d'écosystème":   "#4a9a5a",
    "Nouvelle protection":         "#2196f3",
    "Réduction de pollution":      "#00bcd4",
    "Retour d'espèce":             "#8bc34a",
    "Rewilding":                   "#ff9800",
}
WIN_DEFAULT_COLOR = "#4a9a5a"


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
        win   = item.get("win_type", "Régénération d'écosystème")
        color = WIN_COLOR.get(win, WIN_DEFAULT_COLOR)
        cards_html += f"""
        <tr>
          <td style="padding: 0 24px 20px;">
            <table width="100%" cellpadding="0" cellspacing="0"
                   style="background:#0d2b1a; border:1px solid #1a4a2a; border-radius:10px; overflow:hidden;">
              <tr><td style="height:3px; background:{color}; padding:0;"></td></tr>
              <tr>
                <td style="padding:18px 20px;">
                  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:10px;">
                    <tr>
                      <td>
                        <span style="display:inline-block; background:rgba(74,154,90,0.15); color:#6abf6a;
                              border:1px solid rgba(74,154,90,0.3); padding:2px 10px; border-radius:12px;
                              font-size:11px; margin-right:5px;">{item.get("category","")}</span>
                        <span style="display:inline-block; background:rgba(46,184,46,0.08); color:#4a8a5a;
                              border:1px solid rgba(46,184,46,0.15); padding:2px 10px; border-radius:12px;
                              font-size:11px;">{item.get("region","")}</span>
                      </td>
                      <td align="right" style="font-size:22px;">{item.get("emoji","🌿")}</td>
                    </tr>
                  </table>
                  <div style="font-size:15px; font-weight:600; color:#b8e8a0; line-height:1.4; margin-bottom:10px;">
                    {item.get("title","")}
                  </div>
                  <div style="font-size:13px; color:#5a8a6a; line-height:1.6; margin-bottom:14px;">
                    {item.get("summary","")}
                  </div>
                  <table width="100%" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="font-size:11px; color:#2a5a3a;">{item.get("date","")}</td>
                      <td align="right">
                        <span style="display:inline-block; width:6px; height:6px; border-radius:50%;
                              background:{color}; margin-right:5px; vertical-align:middle;"></span>
                        <span style="font-size:10px; color:{color}; letter-spacing:1px;">{win}</span>
                      </td>
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
<body style="margin:0; padding:0; background:#071209; font-family:Georgia,'Times New Roman',serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#071209; padding:24px 0;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0"
             style="background:#0a1a0f; border-radius:14px; overflow:hidden; border:1px solid #1a4a2a;">
        <tr>
          <td style="background:linear-gradient(135deg,#0d2b1a 0%,#0a1a0f 60%,#071209 100%);
                     padding:32px 28px 24px; border-bottom:1px solid #1a4a2a;">
            <div style="font-size:11px; letter-spacing:4px; color:#4a9a5a; text-transform:uppercase; margin-bottom:6px;">
              Agent IA · Bonnes nouvelles de la nature
            </div>
            <div style="font-size:26px; font-weight:700; color:#c8f0b0;">🌿 La Nature Résiste</div>
            <div style="font-size:13px; color:#3a6a4a; margin-top:8px;">Semaine du {date_str}</div>
          </td>
        </tr>
        {cards_html}
        <tr>
          <td style="padding:20px 24px; border-top:1px solid #1a4a2a; text-align:center;">
            <div style="font-size:11px; color:#2a5a3a;">
              {len(news)} victoires pour la nature · Sources : web, presse scientifique et environnementale
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


def build_web_page(news):
    date_str = datetime.now().strftime("%A %d %B %Y")
    updated  = datetime.now().strftime("%d/%m/%Y à %H:%M")

    cards_html = ""
    for item in news:
        win   = item.get("win_type", "Régénération d'écosystème")
        color = WIN_COLOR.get(win, WIN_DEFAULT_COLOR)
        cards_html += f"""
        <div class="card" data-region="{item.get('region','')}">
          <div class="card-bar" style="background:{color}"></div>
          <div class="card-body">
            <div class="card-meta">
              <div class="badges">
                <span class="badge badge-cat">{item.get("category","")}</span>
                <span class="badge badge-reg">{item.get("region","")}</span>
              </div>
              <span class="emoji">{item.get("emoji","🌿")}</span>
            </div>
            <h2 class="card-title">{item.get("title","")}</h2>
            <p class="card-summary">{item.get("summary","")}</p>
            <div class="card-footer">
              <span class="card-date">{item.get("date","")}{(' · <a class="source-link" href="' + (item.get("source_url") or item.get("url","")) + '" target="_blank" rel="noopener">→ source</a>') if (item.get("source_url") or item.get("url")) else ""}</span>
              <span class="win-badge" style="color:{color}">
                <span class="win-dot" style="background:{color}"></span>
                {win}
              </span>
            </div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>🌿 La Nature Résiste</title>
  <meta name="description" content="Les bonnes nouvelles de la nature cette semaine — espèces qui reviennent, écosystèmes qui se régénèrent.">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #0a1a0f;
      color: #e8f5e0;
      font-family: Georgia, 'Times New Roman', serif;
      min-height: 100vh;
    }}
    header {{
      background: linear-gradient(135deg, #0d2b1a 0%, #0a1a0f 60%, #071209 100%);
      border-bottom: 1px solid #1a4a2a;
      padding: 40px 24px 28px;
      text-align: center;
    }}
    .header-label {{
      font-size: 11px;
      letter-spacing: 4px;
      color: #4a9a5a;
      text-transform: uppercase;
      margin-bottom: 10px;
      font-family: -apple-system, sans-serif;
    }}
    h1 {{
      font-size: clamp(24px, 5vw, 36px);
      font-weight: 700;
      color: #c8f0b0;
      margin-bottom: 8px;
    }}
    .header-date {{
      font-size: 13px;
      color: #3a6a4a;
      margin-top: 6px;
      font-family: -apple-system, sans-serif;
    }}
    /* Tabs */
    .tabs {{
      display: flex;
      justify-content: center;
      gap: 8px;
      padding: 24px 20px 0;
      max-width: 1100px;
      margin: 0 auto;
    }}
    .tab {{
      padding: 7px 22px;
      border-radius: 20px;
      border: 1px solid #1a4a2a;
      background: transparent;
      color: #4a7a5a;
      font-size: 13px;
      cursor: pointer;
      font-family: -apple-system, sans-serif;
      transition: all .2s;
    }}
    .tab:hover {{ border-color: #2a6a3a; color: #6abf6a; }}
    .tab.active {{
      border-color: #4a9a5a;
      background: rgba(74,154,90,0.15);
      color: #8fdc8f;
    }}
    /* Grid */
    #grid {{
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px 20px 36px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 20px;
    }}
    .card {{
      background: linear-gradient(135deg, #0d2b1a 0%, #0a2015 100%);
      border: 1px solid #1a4a2a;
      border-radius: 12px;
      overflow: hidden;
      transition: transform .2s, border-color .2s;
    }}
    .card:hover {{ transform: translateY(-3px); border-color: #2a6a3a; }}
    .card.hidden {{ display: none; }}
    .card-bar {{ height: 3px; }}
    .card-body {{ padding: 20px; }}
    .card-meta {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      margin-bottom: 12px;
    }}
    .badges {{ display: flex; gap: 6px; flex-wrap: wrap; }}
    .badge {{
      padding: 3px 10px;
      border-radius: 12px;
      font-size: 11px;
      font-family: -apple-system, sans-serif;
    }}
    .badge-cat {{
      background: rgba(74,154,90,0.15);
      color: #6abf6a;
      border: 1px solid rgba(74,154,90,0.3);
    }}
    .badge-reg {{
      background: rgba(46,184,46,0.08);
      color: #4a8a5a;
      border: 1px solid rgba(46,184,46,0.15);
    }}
    .emoji {{ font-size: 24px; }}
    .card-title {{
      font-size: 15px;
      font-weight: 600;
      color: #b8e8a0;
      line-height: 1.4;
      margin-bottom: 10px;
    }}
    .card-summary {{
      font-size: 13px;
      color: #5a8a6a;
      line-height: 1.6;
      margin-bottom: 14px;
      font-family: -apple-system, sans-serif;
    }}
    .card-footer {{
      display: flex;
      justify-content: space-between;
      align-items: center;
    }}
    .card-date {{ font-size: 11px; color: #2a5a3a; font-family: -apple-system, sans-serif; }}
    .source-link {{
      font-size: 11px;
      color: #4a9a5a;
      text-decoration: none;
      font-family: -apple-system, sans-serif;
      letter-spacing: .3px;
    }}
    .source-link:hover {{ text-decoration: underline; }}
    .win-badge {{
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 10px;
      letter-spacing: .8px;
      font-family: -apple-system, sans-serif;
    }}
    .win-dot {{ width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }}
    .empty-msg {{
      grid-column: 1/-1;
      text-align: center;
      padding: 60px 20px;
      color: #2a5a3a;
      font-family: -apple-system, sans-serif;
      font-size: 14px;
    }}
    footer {{
      text-align: center;
      padding: 24px;
      font-size: 11px;
      color: #2a5a3a;
      border-top: 1px solid #1a4a2a;
      font-family: -apple-system, sans-serif;
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-label">Agent IA · Bonnes nouvelles de la nature</div>
    <h1>🌿 La Nature Résiste</h1>
    <div class="header-date">Semaine du {date_str}</div>
  </header>

  <div class="tabs">
    <button class="tab active" data-filter="all">🌍 Toutes</button>
    <button class="tab" data-filter="europe">🇪🇺 Europe</button>
    <button class="tab" data-filter="world">🌐 Reste du monde</button>
  </div>

  <div id="grid">
    {cards_html}
    <div class="empty-msg" id="empty-msg" style="display:none">Aucune actualité pour ce filtre cette semaine.</div>
  </div>

  <footer>
    {len(news)} victoires pour la nature · Mis à jour le {updated} · Sources : web, presse scientifique et environnementale
  </footer>

  <script>
    const tabs = document.querySelectorAll('.tab');
    const cards = document.querySelectorAll('.card');
    const emptyMsg = document.getElementById('empty-msg');

    tabs.forEach(tab => {{
      tab.addEventListener('click', () => {{
        tabs.forEach(t => t.classList.remove('active'));
        tab.classList.add('active');

        const filter = tab.dataset.filter;
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


def publish_to_github(news):
    """Generate index.html and push to GitHub Pages."""
    html = build_web_page(news)
    with open(WEB_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    date_str = datetime.now().strftime("%d %B %Y")
    env = {k: v for k, v in os.environ.items()}
    cmds = [
        ["git", "add", "index.html"],
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
