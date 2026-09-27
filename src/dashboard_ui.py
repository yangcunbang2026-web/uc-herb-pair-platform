from __future__ import annotations

import html
from typing import Any

import streamlit as st


def inject_dashboard_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --uc-bg: oklch(1 0 0);
            --uc-surface: oklch(0.97 0.008 200);
            --uc-surface-strong: oklch(0.935 0.017 200);
            --uc-ink: oklch(0.22 0.025 205);
            --uc-muted: oklch(0.43 0.022 205);
            --uc-primary: oklch(0.45 0.074 200);
            --uc-primary-soft: oklch(0.93 0.025 200);
            --uc-border: oklch(0.86 0.014 200);
            --uc-success: oklch(0.46 0.10 155);
            --uc-warning: oklch(0.55 0.12 75);
            --uc-pending: oklch(0.52 0.015 220);
        }
        [data-testid="stAppViewContainer"] {
            background: var(--uc-bg);
            color: var(--uc-ink);
        }
        [data-testid="stHeader"] { background: color-mix(in oklch, var(--uc-bg) 92%, transparent); }
        h1, h2, h3 { color: var(--uc-ink); text-wrap: balance; letter-spacing: -0.02em; }
        p, li, [data-testid="stCaptionContainer"] { text-wrap: pretty; }
        [data-testid="stCaptionContainer"] { color: var(--uc-muted); }
        [data-testid="stMetric"] {
            background: var(--uc-surface);
            border: 1px solid var(--uc-border);
            border-radius: 12px;
            padding: 0.9rem 1rem;
        }
        [data-testid="stMetricValue"] { color: var(--uc-primary); }
        [data-testid="stExpander"] {
            border-color: var(--uc-border);
            border-radius: 12px;
            background: var(--uc-bg);
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 8px;
            border-color: var(--uc-primary);
            color: var(--uc-primary);
            min-height: 2.6rem;
        }
        .stButton > button[kind="primary"],
        [data-testid="stFormSubmitButton"] button,
        .stFormSubmitButton button {
            border-color: var(--uc-primary) !important;
            background: var(--uc-primary) !important;
            color: white !important;
        }
        .stButton > button[kind="primary"]:hover,
        [data-testid="stFormSubmitButton"] button:hover,
        .stFormSubmitButton button:hover {
            border-color: oklch(0.39 0.074 200) !important;
            background: oklch(0.39 0.074 200) !important;
            color: white !important;
        }
        .stButton > button:focus-visible, .stDownloadButton > button:focus-visible,
        [data-baseweb="select"]:focus-within {
            outline: 3px solid var(--uc-primary-soft);
            outline-offset: 2px;
        }
        [data-testid="stDataFrame"] { border: 1px solid var(--uc-border); border-radius: 12px; }
        .research-funnel {
            display: grid;
            grid-template-columns: repeat(6, minmax(130px, 1fr));
            gap: 0.75rem;
            margin: 0.4rem 0 1rem;
        }
        .funnel-step {
            position: relative;
            min-height: 126px;
            padding: 0.9rem;
            border: 1px solid var(--uc-border);
            border-radius: 12px;
            background: var(--uc-bg);
        }
        .funnel-step::after {
            content: "→";
            position: absolute;
            right: -0.66rem;
            top: 48px;
            z-index: 1;
            padding: 0 0.18rem;
            background: var(--uc-bg);
            color: var(--uc-muted);
            font-weight: 700;
        }
        .funnel-step:last-child::after { display: none; }
        .funnel-step.done { border-color: color-mix(in oklch, var(--uc-success) 45%, var(--uc-border)); }
        .funnel-step.active { border-color: color-mix(in oklch, var(--uc-primary) 52%, var(--uc-border)); }
        .funnel-step.pending { background: var(--uc-surface); }
        .funnel-name {
            display: block;
            margin-bottom: 0.55rem;
            color: var(--uc-muted);
            font-size: 0.84rem;
            font-weight: 650;
        }
        .funnel-value {
            display: block;
            color: var(--uc-ink);
            font-size: 1.16rem;
            font-weight: 760;
            line-height: 1.3;
        }
        .funnel-note {
            display: block;
            margin-top: 0.55rem;
            color: var(--uc-muted);
            font-size: 0.78rem;
            line-height: 1.45;
        }
        .evidence-callout {
            padding: 0.9rem 1rem;
            border: 1px solid var(--uc-border);
            border-radius: 12px;
            background: var(--uc-surface);
            color: var(--uc-ink);
            line-height: 1.55;
        }
        @media (max-width: 1100px) {
            .research-funnel { grid-template-columns: repeat(3, minmax(180px, 1fr)); }
            .funnel-step:nth-child(3)::after { display: none; }
        }
        @media (max-width: 700px) {
            .research-funnel { grid-template-columns: 1fr; }
            .funnel-step { min-height: auto; }
            .funnel-step::after { display: none; }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_reference_layout_theme() -> None:
    """Visual system copied from the approved long-page reference."""
    st.markdown(
        """
        <style>
        :root {
            --page-bg: #f4f6f2;
            --navy: #102f3f;
            --navy-2: #184758;
            --teal: #237c70;
            --teal-soft: #eaf4f1;
            --orange: #ef962f;
            --orange-soft: #fff5e6;
            --blue: #3789a6;
            --blue-soft: #edf6fa;
            --green: #5e8c50;
            --green-soft: #eff7ed;
            --ink: #183746;
            --muted: #617681;
            --line: #dce6e2;
            --white: #ffffff;
        }
        [data-testid="stAppViewContainer"] {
            background: var(--page-bg);
            color: var(--ink);
        }
        [data-testid="stHeader"] {
            background: rgba(244, 246, 242, 0.93);
        }
        [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none; }
        [data-testid="stMainBlockContainer"], .block-container {
            max-width: 1180px;
            padding-top: 1.1rem;
            padding-bottom: 5rem;
        }
        h1, h2, h3, h4, p { color: var(--ink); }
        h1, h2, h3 { letter-spacing: -0.025em; }
        [data-testid="stSidebar"] { background: #edf1f0; }

        .portal-nav {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1.5rem;
            padding: 1rem 1.35rem;
            border-radius: 16px;
            background: var(--navy);
            color: white;
            margin: 0 0 1.1rem;
        }
        .portal-brand {
            color: white;
            font-size: 1.02rem;
            font-weight: 780;
            white-space: nowrap;
        }
        .portal-links {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 1.15rem;
        }
        .portal-links a {
            color: #d7e7ec;
            text-decoration: none;
            font-size: 0.84rem;
            font-weight: 650;
        }
        .portal-links a:hover, .portal-links a:focus-visible { color: white; }

        .portal-hero {
            padding: 2.25rem 2.35rem;
            border-radius: 24px;
            background: var(--navy-2);
            color: white;
            margin-bottom: 1.65rem;
        }
        .hero-kicker {
            margin: 0 0 0.55rem;
            color: #62d8c7;
            font-size: 0.92rem;
            font-weight: 760;
        }
        .portal-hero h1 {
            max-width: 920px;
            margin: 0;
            color: white;
            font-size: 2.35rem;
            line-height: 1.16;
            letter-spacing: -0.035em;
        }
        .portal-hero p {
            max-width: 820px;
            margin: 0.85rem 0 1.35rem;
            color: #d5e6eb;
            font-size: 1rem;
        }
        .hero-actions { display: flex; flex-wrap: wrap; gap: 0.75rem; }
        .hero-actions a {
            display: inline-flex;
            align-items: center;
            min-height: 42px;
            padding: 0.65rem 1.15rem;
            border-radius: 8px;
            background: var(--orange);
            color: white;
            text-decoration: none;
            font-weight: 720;
        }
        .hero-actions a.secondary {
            background: #315e6c;
            color: #f4fbfd;
        }

        .section-label {
            margin: 1.5rem 0 0.75rem;
            color: #58717c;
            font-size: 0.92rem;
            font-weight: 720;
        }
        .metric-strip {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.85rem;
            margin-bottom: 1.45rem;
        }
        .metric-tile {
            min-height: 110px;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 0.9rem;
            border-radius: 16px;
            background: white;
            box-shadow: 0 7px 14px rgba(16, 47, 63, 0.07);
            text-align: center;
        }
        .metric-tile.final { background: var(--navy-2); }
        .metric-number {
            color: var(--navy);
            font-size: 1.65rem;
            font-weight: 820;
            line-height: 1;
        }
        .metric-tile.final .metric-number,
        .metric-tile.final .metric-text { color: white; }
        .metric-text { margin-top: 0.65rem; color: var(--muted); font-size: 0.84rem; }

        .chapter-nav {
            display: grid;
            grid-template-columns: repeat(7, minmax(0, 1fr));
            gap: 0.2rem;
            padding: 0.8rem 0.95rem;
            border-radius: 12px;
            background: #e5efed;
            margin-bottom: 1.6rem;
        }
        .chapter-nav a {
            padding: 0.35rem 0.25rem;
            color: #375967;
            text-align: center;
            text-decoration: none;
            font-size: 0.78rem;
            font-weight: 720;
        }
        .chapter-anchor { scroll-margin-top: 5rem; }

        [data-testid="stVerticalBlockBorderWrapper"] {
            border: 1px solid var(--line);
            border-radius: 16px;
            background: white;
            box-shadow: 0 6px 12px rgba(16, 47, 63, 0.055);
        }
        .chapter-copy { padding: 0.2rem 0.15rem; }
        .chapter-index {
            margin-bottom: 0.35rem;
            color: #66808b;
            font-size: 0.84rem;
            font-weight: 760;
        }
        .chapter-title {
            margin: 0 0 0.75rem;
            color: var(--navy);
            font-size: 1.48rem;
            line-height: 1.25;
            font-weight: 820;
        }
        .chapter-line {
            display: grid;
            grid-template-columns: 7.4rem minmax(0, 1fr);
            gap: 0.7rem;
            margin-top: 0.38rem;
            color: #536b76;
            font-size: 0.91rem;
            line-height: 1.55;
        }
        .chapter-line strong { color: var(--teal); }
        .chapter-line.what strong { color: #b97918; }
        .chapter-hint { margin-top: 0.45rem; color: #7d8e96; font-size: 0.83rem; }
        .chapter-accent {
            width: 7px;
            min-height: 152px;
            border-radius: 999px;
            background: var(--teal);
        }
        .chapter-accent.orange { background: var(--orange); }
        .chapter-accent.blue { background: var(--blue); }
        .chapter-accent.green { background: var(--green); }
        .chapter-gap { height: 0.85rem; }

        .stButton > button, .stDownloadButton > button {
            min-height: 42px;
            border: 0;
            border-radius: 8px;
            background: var(--teal);
            color: white;
            font-weight: 720;
        }
        .stButton > button:hover, .stDownloadButton > button:hover {
            border: 0;
            background: #19675d;
            color: white;
        }
        .stButton > button p, .stDownloadButton > button p {
            color: white !important;
        }
        [data-testid="stFormSubmitButton"] button p,
        .stFormSubmitButton button p {
            color: white !important;
        }
        .stButton > button:focus-visible, .stDownloadButton > button:focus-visible {
            outline: 3px solid rgba(35, 124, 112, 0.23);
            outline-offset: 2px;
        }
        [data-testid="stExpander"] {
            border: 1px solid var(--line);
            border-radius: 12px;
            background: white;
        }
        [data-testid="stMetric"] {
            border: 0;
            border-radius: 10px;
            background: #edf4f2;
            box-shadow: none;
        }
        [data-testid="stDataFrame"] { border-radius: 10px; overflow: hidden; }

        .top10-panel {
            padding: 1.7rem 1.8rem 1.3rem;
            border-radius: 22px;
            background: var(--navy-2);
            color: white;
        }
        .top10-panel h2 { margin: 0.2rem 0 0.55rem; color: white; }
        .top10-panel p { color: #d8e8ec; }
        .top10-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        .top10-table th {
            padding: 0.72rem 0.8rem;
            background: #27596a;
            color: #9fe1d5;
            text-align: left;
            font-size: 0.79rem;
        }
        .top10-table td {
            padding: 0.72rem 0.8rem;
            border-bottom: 1px solid rgba(255,255,255,0.11);
            color: #f4fbfd;
            font-size: 0.84rem;
        }
        .top10-foot { margin-top: 0.7rem; color: #bcd2d9; font-size: 0.82rem; }

        .evidence-flow {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.25rem;
            padding: 0.9rem;
            border-radius: 12px;
            background: #e5efed;
            color: #45626e;
            text-align: center;
            font-size: 0.79rem;
            font-weight: 700;
        }
        .boundary-note {
            padding: 0.9rem 1rem;
            border-radius: 10px;
            background: #eef4f2;
            color: #4d6670;
            font-size: 0.87rem;
            line-height: 1.55;
        }

        @media (max-width: 900px) {
            .portal-links { display: none; }
            .portal-hero { padding: 1.7rem 1.4rem; }
            .portal-hero h1 { font-size: 1.9rem; }
            .metric-strip { grid-template-columns: repeat(2, 1fr); }
            .metric-tile.final { grid-column: span 2; }
            .chapter-nav { grid-template-columns: repeat(2, 1fr); }
            .evidence-flow { grid-template-columns: repeat(2, 1fr); }
        }
        @media (max-width: 620px) {
            .metric-strip { grid-template-columns: 1fr; }
            .metric-tile.final { grid-column: auto; }
            .chapter-nav { grid-template-columns: 1fr; }
            .chapter-line { grid-template-columns: 1fr; gap: 0.1rem; }
            .chapter-accent { min-height: 8px; width: 100%; }
            .top10-panel { padding: 1.25rem 1rem; overflow-x: auto; }
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_screening_funnel(steps: list[dict[str, Any]]) -> None:
    nodes: list[str] = []
    for step in steps:
        state = str(step.get("state", "pending"))
        if state not in {"done", "active", "pending"}:
            state = "pending"
        nodes.append(
            '<div class="funnel-step {state}" role="listitem">'
            '<span class="funnel-name">{name}</span>'
            '<strong class="funnel-value">{value}</strong>'
            '<span class="funnel-note">{note}</span>'
            "</div>".format(
                state=state,
                name=html.escape(str(step.get("name", ""))),
                value=html.escape(str(step.get("value", ""))),
                note=html.escape(str(step.get("note", ""))),
            )
        )
    st.markdown(
        '<div class="research-funnel" role="list" aria-label="药对筛选漏斗">'
        + "".join(nodes)
        + "</div>",
        unsafe_allow_html=True,
    )

