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
import json
import os
import sys
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

    print(f"\n✅ Done. Report saved to: {path.relative_to(HERE)}\n")
    print(markdown)


if __name__ == "__main__":
    main()
