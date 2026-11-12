"""Streamlit interface:  streamlit run app.py"""
from __future__ import annotations

import html
import json

import streamlit as st

from codereviewer.config import ConfigError, Settings
from codereviewer.ingest import LANGUAGES, IngestError, from_text, language_for, source_from_bytes
from codereviewer.pipeline import CodeReviewer
from codereviewer.providers import create_provider
from codereviewer.render import to_html, to_markdown, unified_diff

st.set_page_config(page_title="Intelligent Code Reviewer", page_icon="🔍", layout="wide")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@500;700&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', system-ui, sans-serif; }
h1, h2, h3 { font-family: 'JetBrains Mono', ui-monospace, monospace; letter-spacing: -0.02em; }
.block-container { max-width: 1200px; padding-top: 2rem; }
.cr-lede { color: #5B6878; margin: -0.5rem 0 1.2rem; max-width: 70ch; }
.cr-finding { border-left: 4px solid var(--c); background: #fff; padding: 10px 14px; margin: 8px 0;
              border-radius: 0 8px 8px 0; border-top: 1px solid #E3E8EE; border-right: 1px solid #E3E8EE;
              border-bottom: 1px solid #E3E8EE; }
.cr-tag { font: 700 .72rem 'JetBrains Mono', monospace; color: var(--c); letter-spacing: .04em; }
.cr-meta { color: #5B6878; font-size: .85rem; }
.cr-fix { color: #1F6B4A; margin-top: 4px; }
</style>
""",
    unsafe_allow_html=True,
)

SEVERITY_COLOR = {"HIGH": "#B3261E", "MEDIUM": "#9A5B00", "LOW": "#1E6FA8", None: "#5B6878"}
SAMPLE = '''def add_item(item, stock=[]):
    stock.append(item)
    return stock


def average(prices):
    total = 0
    for i in range(len(prices) + 1):
        total += prices[i]
    return total / len(prices)


def find_user(cur, name):
    cur.execute(f"SELECT * FROM users WHERE name = '{name}'")
    return cur.fetchone()
'''


def load_sample() -> None:
    st.session_state["pasted_code"] = SAMPLE
    st.session_state["paste_language"] = "python"


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #

env = Settings.from_env()
with st.sidebar:
    st.subheader("Engine")
    provider_name = st.selectbox("Provider", ["gemini", "openai", "mock"],
                                 index=["gemini", "openai", "mock"].index(env.provider), key="provider")
    default_model = {"gemini": env.gemini_model, "openai": env.openai_model, "mock": "mock-reviewer"}[provider_name]
    model_name = st.text_input("Model", value=default_model, key=f"model_{provider_name}", disabled=provider_name == "mock")
    thinking = st.selectbox("Gemini thinking level", ["(default)", "minimal", "low", "medium", "high"],
                            key="thinking", disabled=provider_name != "gemini")
    override_temp = st.checkbox("Override temperature", key="override_temp",
                                help="Leave off to use the provider default. Google recommends the default for Gemini 3.")
    temperature = st.slider("Temperature", 0.0, 2.0, 0.2, 0.05, key="temperature", disabled=not override_temp)
    explain = st.toggle("Explain the code in plain language", value=True, key="explain")


@st.cache_resource(show_spinner=False)
def get_reviewer(provider: str, model: str, thinking_level: str | None, temp: float | None):
    overrides = {"provider": provider, "gemini_thinking_level": thinking_level, "temperature": temp}
    overrides["gemini_model" if provider == "gemini" else "openai_model"] = model or None
    settings = Settings.from_env(**overrides)
    return CodeReviewer(create_provider(settings), settings), settings


try:
    reviewer, settings = get_reviewer(
        provider_name, model_name.strip(), None if thinking == "(default)" else thinking,
        temperature if override_temp else None,
    )
except ConfigError as exc:
    st.error(str(exc))
    st.stop()

missing_key = provider_name != "mock" and not settings.api_key
with st.sidebar:
    if provider_name == "mock":
        st.warning("Demo engine: only deterministic checks run. No language model reviews the code.")
    elif missing_key:
        st.error(f"API key for {provider_name} is not set. Copy .env.example to .env, add it, restart the app.")
    else:
        st.success(f"Using {reviewer.provider.model}")

# --------------------------------------------------------------------------- #
# Input
# --------------------------------------------------------------------------- #

st.title("Intelligent Code Reviewer & Explainer")
st.markdown('<p class="cr-lede">Upload a source file or paste code. You get a plain-language explanation, a bug report '
            "with line numbers, and a corrected version with a diff.</p>", unsafe_allow_html=True)

tab_upload, tab_paste = st.tabs(["Upload a file", "Paste code"])
with tab_upload:
    uploaded = st.file_uploader("Source file", type=[ext.lstrip(".") for ext in LANGUAGES], key="upload")
with tab_paste:
    lang_choice = st.selectbox("Language", sorted(set(LANGUAGES.values())), key="paste_language")
    pasted = st.text_area("Code", height=260, key="pasted_code", placeholder="Paste code here")
    st.button("Load an example with bugs", on_click=load_sample, key="example")

go = st.button("Review code", type="primary", key="review", disabled=missing_key)

if go:
    try:
        if uploaded is not None:
            source = source_from_bytes(uploaded.getvalue(), uploaded.name, language_for(uploaded.name))
        else:
            source = from_text(pasted, "pasted_code", lang_choice)
        with st.spinner("Reviewing…"):
            st.session_state["result"] = reviewer.review(source, explain=explain)
        st.session_state["input_error"] = None
    except IngestError as exc:
        st.session_state["result"] = None
        st.session_state["input_error"] = str(exc)
    except ConfigError as exc:
        st.session_state["result"] = None
        st.session_state["input_error"] = str(exc)
    st.rerun()

# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

if st.session_state.get("input_error"):
    st.error(st.session_state["input_error"])

result = st.session_state.get("result")
if result is not None:
    if not result.ok:
        st.error(result.error)
        with st.expander("Rejected model response"):
            st.code(result.chunks[-1].raw if result.chunks else "", language=None)
    else:
        for note in result.notes + result.warnings:
            st.info(note)
        counts = result.counts()
        cols = st.columns(5)
        cols[0].metric("High", counts["HIGH"])
        cols[1].metric("Medium", counts["MEDIUM"])
        cols[2].metric("Low", counts["LOW"])
        cols[3].metric("Tokens", result.prompt_tokens + result.completion_tokens)
        cols[4].metric("Seconds", f"{result.latency_s:.1f}")

        t_exp, t_bugs, t_fix, t_diff, t_orig = st.tabs(
            ["Explanation", f"Bug report ({len(result.findings)})", "Refactored code", "Changes", "Original"])
        with t_exp:
            if result.explanation:
                st.markdown("\n".join(f"- {e}" for e in result.explanation))
            else:
                st.caption("The explanation was switched off.")
        with t_bugs:
            if not result.findings:
                st.success("No defects found.")
            for f in result.findings:
                color = SEVERITY_COLOR.get(f.severity, SEVERITY_COLOR[None])
                where = (f"line {f.line}" if f.line else "") + (f"-{f.end_line}" if f.end_line else "")
                origin = " | static pre-check" if f.source == "static" else ""
                esc = html.escape                      # model text is untrusted: always escape before HTML
                fix = f'<div class="cr-fix">Fix: {esc(f.fix)}</div>' if f.fix else ""
                st.markdown(
                    f'<div class="cr-finding" style="--c:{color}"><span class="cr-tag">{esc(f.severity or "NOTE")} '
                    f'{esc(f.category or "")}</span> <span class="cr-meta">{esc(where)}{origin}</span>'
                    f"<div>{esc(f.message)}</div>{fix}</div>",
                    unsafe_allow_html=True,
                )
        with t_fix:
            for start, end, code in result.refactored_parts:
                if len(result.refactored_parts) > 1:
                    st.caption(f"Lines {start}-{end}")
                st.code(code, language=result.source.language, line_numbers=True)
        with t_diff:
            diff = unified_diff(result)
            st.code(diff, language="diff") if diff else st.caption("No changes.")
        with t_orig:
            st.code(result.source.text, language=result.source.language, line_numbers=True)

        d1, d2, d3 = st.columns(3)
        d1.download_button("Report (.md)", to_markdown(result), f"{result.source.name}.review.md", key="dl_md")
        d2.download_button("Report (.html)", to_html(result, show_diff=True), f"{result.source.name}.review.html", key="dl_html")
        d3.download_button("Data (.json)", json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                           f"{result.source.name}.review.json", "application/json", key="dl_json")
else:
    st.info("Paste code or upload a file, then choose Review code. Try the example to see the whole flow.")
