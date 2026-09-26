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

