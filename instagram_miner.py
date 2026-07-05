#!/usr/bin/env python3
"""
Dina — daily Instagram content miner.

Two phases, both powered by Claude (Anthropic API):

  1. MINE   — Claude uses live web search to find what's currently resonating
              in your niche (competitor posts, trending formats, topics).
  2. CREATE — Claude turns that research into 3 post ideas + 3 trial-reel ideas,
              each written in your brand voice and tied back to your offer,
              returned as validated structured JSON.

The result is rendered to a dated Markdown report in ./reports/.

Run:   python instagram_miner.py
Needs: ANTHROPIC_API_KEY in the environment, and a config.yaml next to this file.
"""
from __future__ import annotations

import datetime as dt
import html as html_lib
import json
import os
import smtplib
import ssl
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

try:
    import anthropic
except ImportError:
    sys.exit("The 'anthropic' package is missing. Run:  pip install -r requirements.txt")

HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
#  Config                                                                      #
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    cfg_path = HERE / "config.yaml"
    if not cfg_path.exists():
        sys.exit(f"Config not found: {cfg_path}. Copy config.yaml and edit it.")
    with cfg_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if "brand" not in cfg or not cfg["brand"].get("niche"):
        sys.exit("config.yaml is missing brand.niche — fill in your niche first.")
    cfg.setdefault("model", "claude-opus-4-8")
    cfg.setdefault("num_post_ideas", 3)
    cfg.setdefault("num_reel_ideas", 3)
    cfg.setdefault("output_dir", "reports")
    cfg.setdefault("competitors", [])
    cfg.setdefault("hashtags", [])
    cfg.setdefault("avoid", [])
    cfg.setdefault("email", {})
    cfg["email"].setdefault("subject_prefix", "📈 Instagram ideas")
    return cfg


def _brand_block(cfg: dict) -> str:
    b = cfg["brand"]
    lines = [
        f"- Niche: {b.get('niche', '').strip()}",
        f"- Brand voice: {b.get('voice', '').strip()}",
        f"- Target audience: {b.get('target_audience', '').strip()}",
        f"- Offer to funnel toward: {b.get('offer', '').strip()}",
    ]
    if cfg["competitors"]:
        lines.append(f"- Creators/accounts to study: {', '.join(map(str, cfg['competitors']))}")
    if cfg["hashtags"]:
        lines.append(f"- Hashtags / search terms: {', '.join(map(str, cfg['hashtags']))}")
    if cfg["avoid"]:
        lines.append(f"- Avoid entirely: {'; '.join(map(str, cfg['avoid']))}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
#  Phase 1 — MINE (live web search)                                           #
# --------------------------------------------------------------------------- #
def research_trends(client: anthropic.Anthropic, cfg: dict, today: str) -> str:
    """Ask Claude to search the web for what's currently working in the niche."""
    prompt = f"""Today is {today}. You are a sharp social-media strategist doing daily \
content research for a creator. Here is their brand:

{_brand_block(cfg)}

Use web search to find what is ACTUALLY resonating right now (roughly the last 2-4 weeks) \
in and around this niche on Instagram and adjacent platforms. Look for:
  - Specific recent posts/reels from the named creators (or similar ones) that appear to \
have performed well, and WHY they worked (hook, format, angle, emotion).
  - Trending post formats, content angles, hot takes, and topics in this space.
  - Any timely news, tools, or conversations the creator could ride.

Then write a concise research brief (bullet points, ~250-400 words). For each item, note \
the source/creator and the reusable *pattern* — the thing that could be reframed for this \
brand, not just the specific post. Do not write the final post ideas yet; just the raw \
intelligence. Prefer concrete, recent, specific findings over generic advice."""

    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]

    # Server-side web search runs a loop that can pause; resume until it finishes.
    for _ in range(6):
        resp = client.messages.create(
            model=cfg["model"],
            max_tokens=4000,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        if resp.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": resp.content})
            continue
        if resp.stop_reason == "refusal":
            return "(Research step was declined by the safety system; proceeding without it.)"
        break

    text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
    return text or "(No research text was returned.)"


# --------------------------------------------------------------------------- #
#  Phase 2 — CREATE (structured output)                                        #
# --------------------------------------------------------------------------- #
def _idea_schema(cfg: dict) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["theme_of_the_day", "post_ideas", "reel_ideas"],
        "properties": {
            "theme_of_the_day": {"type": "string"},
            "post_ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "title", "source_inspiration", "format", "hook",
                        "example_caption", "why_it_fits", "funnel_tie_in",
                    ],
                    "properties": {
                        "title": {"type": "string"},
                        "source_inspiration": {"type": "string"},
                        "format": {"type": "string"},
                        "hook": {"type": "string"},
                        "example_caption": {"type": "string"},
                        "why_it_fits": {"type": "string"},
                        "funnel_tie_in": {"type": "string"},
                    },
                },
            },
            "reel_ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "concept", "hook", "script", "shot_list",
                        "audio_or_trend", "on_screen_text", "cta",
                    ],
                    "properties": {
                        "concept": {"type": "string"},
                        "hook": {"type": "string"},
                        "script": {"type": "string"},
                        "shot_list": {"type": "array", "items": {"type": "string"}},
                        "audio_or_trend": {"type": "string"},
                        "on_screen_text": {"type": "string"},
                        "cta": {"type": "string"},
                    },
                },
            },
        },
    }


def generate_ideas(client: anthropic.Anthropic, cfg: dict, research: str, today: str) -> dict:
    n_posts = cfg["num_post_ideas"]
    n_reels = cfg["num_reel_ideas"]
    prompt = f"""Today is {today}. You are the creator's ghostwriter and content strategist.

BRAND:
{_brand_block(cfg)}

RESEARCH BRIEF (what's working in the niche right now):
{research}

Using the research, produce exactly {n_posts} strong post ideas and exactly {n_reels} \
"trial reel" ideas (quick, low-production reels worth testing) for the creator to post soon.

Rules:
- Reframe and align to THIS brand's voice and audience — never copy anyone. Each idea must \
be distinct in angle.
- Write the example_caption as a ready-to-post caption in the brand voice (hook first line, \
value, then a soft CTA). Real copy, not a description of copy.
- why_it_fits: 1-2 sentences on why this suits the brand AND why the underlying pattern is \
working right now (reference the research).
- funnel_tie_in: how this specific piece moves a viewer one step toward the offer.
- For reels: script is a tight spoken/voiceover script; shot_list is 3-6 concrete shots; \
audio_or_trend suggests a trending-sound style or on-trend format; on_screen_text is the \
opening text overlay; cta is the closing call to action.
- Be specific and immediately usable. Avoid anything in the brand's 'avoid' list."""

    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": _idea_schema(cfg)}},
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        sys.exit("The idea-generation request was declined by the safety system.")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return json.loads(text)


# --------------------------------------------------------------------------- #
#  Render + save                                                               #
# --------------------------------------------------------------------------- #
def render_markdown(cfg: dict, ideas: dict, research: str, today: str) -> str:
    niche = cfg["brand"]["niche"]
    out = [
        f"# 📈 Daily Instagram Ideas — {today}",
        f"**Niche:** {niche}  ",
        f"**Theme of the day:** {ideas.get('theme_of_the_day', '—')}",
        "",
        "## 📝 Post ideas",
    ]
    for i, p in enumerate(ideas.get("post_ideas", []), 1):
        out += [
            f"### {i}. {p.get('title', '')}",
            f"- **Format:** {p.get('format', '')}",
            f"- **Inspired by:** {p.get('source_inspiration', '')}",
            f"- **Hook:** {p.get('hook', '')}",
            "",
            "**Example caption**",
            "",
            "> " + p.get("example_caption", "").replace("\n", "\n> "),
            "",
            f"- **Why it fits your brand:** {p.get('why_it_fits', '')}",
            f"- **Funnel tie-in:** {p.get('funnel_tie_in', '')}",
            "",
        ]

    out.append("## 🎬 Trial reel ideas")
    for i, r in enumerate(ideas.get("reel_ideas", []), 1):
        shots = r.get("shot_list", []) or []
        out += [
            f"### {i}. {r.get('concept', '')}",
            f"- **Hook (first 1s):** {r.get('hook', '')}",
            f"- **On-screen text:** {r.get('on_screen_text', '')}",
            f"- **Audio / trend:** {r.get('audio_or_trend', '')}",
            "",
            "**Script**",
            "",
            "> " + r.get("script", "").replace("\n", "\n> "),
            "",
            "**Shot list**",
            "",
            *[f"{j}. {shot}" for j, shot in enumerate(shots, 1)],
            "",
            f"- **CTA:** {r.get('cta', '')}",
            "",
        ]

    out += [
        "---",
        "<details><summary>🔍 Research brief used (click to expand)</summary>",
        "",
        research,
        "",
        "</details>",
        "",
        "*Generated by Dina · Claude (claude-opus-4-8) with live web search.*",
    ]
    return "\n".join(out)


def save_report(cfg: dict, markdown: str, today: str) -> Path:
    out_dir = HERE / cfg["output_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
#  Email delivery (optional — only runs if SMTP env vars are set)              #
# --------------------------------------------------------------------------- #
def _esc(text: str) -> str:
    return html_lib.escape(str(text or "")).replace("\n", "<br>")


def render_html(cfg: dict, ideas: dict, today: str) -> str:
    """Build a clean HTML email straight from the structured ideas."""
    niche = html_lib.escape(cfg["brand"]["niche"])
    theme = html_lib.escape(ideas.get("theme_of_the_day", "—"))
    parts = [
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:640px;margin:0 auto;color:#1a1a1a;line-height:1.5">',
        f'<h1 style="font-size:22px;margin:0 0 4px">📈 Daily Instagram Ideas — {today}</h1>',
        f'<p style="margin:0 0 2px;color:#555"><strong>Niche:</strong> {niche}</p>',
        f'<p style="margin:0 0 20px;color:#555"><strong>Theme of the day:</strong> {theme}</p>',
        '<h2 style="font-size:18px;border-bottom:2px solid #eee;padding-bottom:6px">📝 Post ideas</h2>',
    ]
    for i, p in enumerate(ideas.get("post_ideas", []), 1):
        parts.append(
            '<div style="margin:0 0 22px;padding:14px 16px;background:#fafafa;border-radius:8px">'
            f'<h3 style="margin:0 0 8px;font-size:16px">{i}. {_esc(p.get("title"))}</h3>'
            f'<p style="margin:2px 0;color:#555"><strong>Format:</strong> {_esc(p.get("format"))} · '
            f'<strong>Inspired by:</strong> {_esc(p.get("source_inspiration"))}</p>'
            f'<p style="margin:2px 0"><strong>Hook:</strong> {_esc(p.get("hook"))}</p>'
            '<div style="margin:10px 0;padding:10px 14px;background:#fff;border-left:3px solid #833ab4">'
            f'{_esc(p.get("example_caption"))}</div>'
            f'<p style="margin:2px 0;color:#555"><strong>Why it fits:</strong> {_esc(p.get("why_it_fits"))}</p>'
            f'<p style="margin:2px 0;color:#555"><strong>Funnel tie-in:</strong> {_esc(p.get("funnel_tie_in"))}</p>'
            '</div>'
        )
    parts.append('<h2 style="font-size:18px;border-bottom:2px solid #eee;padding-bottom:6px">🎬 Trial reel ideas</h2>')
    for i, r in enumerate(ideas.get("reel_ideas", []), 1):
        shots = "".join(f"<li>{_esc(s)}</li>" for s in (r.get("shot_list") or []))
        parts.append(
            '<div style="margin:0 0 22px;padding:14px 16px;background:#fafafa;border-radius:8px">'
            f'<h3 style="margin:0 0 8px;font-size:16px">{i}. {_esc(r.get("concept"))}</h3>'
            f'<p style="margin:2px 0"><strong>Hook:</strong> {_esc(r.get("hook"))}</p>'
            f'<p style="margin:2px 0;color:#555"><strong>On-screen text:</strong> {_esc(r.get("on_screen_text"))} · '
            f'<strong>Audio/trend:</strong> {_esc(r.get("audio_or_trend"))}</p>'
            '<div style="margin:10px 0;padding:10px 14px;background:#fff;border-left:3px solid #fd1d1d">'
            f'{_esc(r.get("script"))}</div>'
            f'<p style="margin:2px 0"><strong>Shot list:</strong></p><ol style="margin:4px 0 8px">{shots}</ol>'
            f'<p style="margin:2px 0;color:#555"><strong>CTA:</strong> {_esc(r.get("cta"))}</p>'
            '</div>'
        )
    parts.append(
        '<p style="color:#999;font-size:12px;margin-top:24px">'
        'Generated by Dina · Claude (claude-opus-4-8) with live web search.</p></div>'
    )
    return "\n".join(parts)


def send_email(cfg: dict, ideas: dict, markdown: str, today: str) -> bool:
    """Send the report by email. No-op (returns False) unless SMTP env vars are set."""
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    if not (host and user and password):
        return False  # email not configured — silently skip

    port = int(os.environ.get("SMTP_PORT", "587"))
    sender = os.environ.get("EMAIL_FROM", user)
    recipient = os.environ.get("EMAIL_TO", user)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{cfg['email']['subject_prefix']} — {today}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(markdown, "plain", "utf-8"))
    msg.attach(MIMEText(render_html(cfg, ideas, today), "html", "utf-8"))

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context) as server:
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_string())
    else:
        with smtplib.SMTP(host, port) as server:
            server.starttls(context=context)
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_string())
    print(f"📧 Emailed report to {recipient}")
    return True


# --------------------------------------------------------------------------- #
#  Main                                                                        #
# --------------------------------------------------------------------------- #
def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it, then re-run.")

    cfg = load_config()
    today = dt.date.today().isoformat()
    client = anthropic.Anthropic()

    print(f"[1/2] Mining trends for: {cfg['brand']['niche']} …")
    research = research_trends(client, cfg, today)

    print("[2/2] Generating post + reel ideas …")
    ideas = generate_ideas(client, cfg, research, today)

    markdown = render_markdown(cfg, ideas, research, today)
    path = save_report(cfg, markdown, today)
    print(f"\n✅ Done. Report saved to: {path.relative_to(HERE)}")

    try:
        if not send_email(cfg, ideas, markdown, today):
            print("   (Email delivery off — set SMTP_HOST/SMTP_USER/SMTP_PASS to enable it.)")
    except Exception as exc:  # never let email trouble lose the report
        print(f"   ⚠️  Email delivery failed ({exc}). The report is still saved.")

    print()
    print(markdown)


if __name__ == "__main__":
    main()
