"""Streamlit-only panel picker; eligibility and composition stay in panel.py."""

import base64
import html

import streamlit as st

from academic_defense_simulator.panel import (
    DEVILS_ADVOCATE_KEY, archetype_default_icon, image_icon_path, list_icon_choices,
)
from academic_defense_simulator.prompts.panelist_prompts import ARCHETYPE_CONFIG


ROLE_DESCRIPTIONS = {
    "methodology_expert": "Defend your research design, sampling, and methods.",
    "literature_theory_specialist": "Connect your study to prior research and theory.",
    "technical_implementation_reviewer": "Explain how you built it and why you chose that approach.",
    "ethics_practicality_reviewer": "Examine real-world impact, ethical risks, and limitations.",
    "problem_objectives_reviewer": "Sharpen your research problem, objectives, and scope.",
    "statistical_analysis_reviewer": "Justify your analysis, metrics, and interpretation of data.",
    "results_conclusions_reviewer": "Show how your findings support your conclusions.",
    "industry_practice_reviewer": "Defend your choices against professional practice and standards.",
}

_SELECTION_KEY = "panel_picker_selection"


def _current_icon(key: str) -> str:
    icon = st.session_state.get(f"panelist_icon_{key}", archetype_default_icon(key))
    return icon if icon in list_icon_choices() else archetype_default_icon(key)


def _remember_name(key: str) -> None:
    # Non-widget state survives removing a card and selecting it again.
    st.session_state[f"panel_saved_name_{key}"] = st.session_state[f"panelist_name_{key}"]


def _choose_icon(key: str, icon: str) -> None:
    st.session_state[f"panelist_icon_{key}"] = icon


def _render_profile_controls(key: str) -> None:
    title = ARCHETYPE_CONFIG[key]["archetype_title"]
    name_key = f"panelist_name_{key}"
    if name_key not in st.session_state:
        st.session_state[name_key] = st.session_state.get(f"panel_saved_name_{key}", "")
    st.text_input(
        "Panelist name", key=name_key, placeholder="Optional name; Dr. is added automatically",
        help=f"Name for your {title}. Leave blank for a generated name.",
        on_change=_remember_name, args=(key,),
    )
    icon = _current_icon(key)
    with st.popover("Change profile picture", width="stretch"):
        st.caption(f"Profile picture for {title}")
        choices = list_icon_choices()
        for start in range(0, len(choices), 3):
            for cell, choice in zip(st.columns(3), choices[start:start + 3]):
                with cell:
                    path = image_icon_path(choice)
                    label = choice.replace("_", " ").replace("-", " ").title() if path else choice
                    if path:
                        st.image(str(path), width=48)
                    else:
                        st.write(choice)
                    st.button(
                        f"Selected: {label}" if choice == icon else label,
                        key=f"pick_{key}_{choice}", disabled=choice == icon,
                        width="stretch", on_click=_choose_icon, args=(key, choice),
                    )


def panel_customization_inputs(selected: list[str]) -> dict[str, tuple[str, str]]:
    """Pass only seated panelists' card settings to the existing profile builder."""
    return {
        key: (st.session_state.get(f"panelist_name_{key}", ""), _current_icon(key))
        for key in [*selected, DEVILS_ADVOCATE_KEY]
    }


def _icon_html(key: str) -> str:
    icon = _current_icon(key)
    path = image_icon_path(icon)
    if path is None:
        return html.escape(icon)
    mime = "jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else path.suffix[1:]
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f'<img src="data:image/{mime};base64,{encoded}" alt="">'


def _toggle_panelist(key: str) -> None:
    selected = list(st.session_state[_SELECTION_KEY])
    if key in selected:
        selected.remove(key)
    elif len(selected) < 3:
        selected.append(key)
    st.session_state[_SELECTION_KEY] = selected


def _restore_recommended(roster: list[str]) -> None:
    st.session_state[_SELECTION_KEY] = roster[:3]


def render_panel_selection(roster: list[str]) -> list[str]:
    """Keep valid choices across reruns and type changes, in composition order."""
    if _SELECTION_KEY not in st.session_state:
        st.session_state[_SELECTION_KEY] = roster[:3]
    previous = st.session_state[_SELECTION_KEY]
    selected = [key for key in roster if key in previous][:3]
    removed = [key for key in previous if key not in roster]
    st.session_state[_SELECTION_KEY] = selected

    st.markdown("""
        <style>
        .st-key-panel_picker {
            border-top: 3px solid #8C3A3F !important;
            background: linear-gradient(135deg, #211917, #16130F 65%);
        }
        .ads-picker-eyebrow { color: #D2A24C; font-size: .76rem;
            letter-spacing: .13em; text-transform: uppercase; margin-bottom: .4rem; }
        .ads-picker-card { min-height: 160px; }
        .ads-picker-card h4 { color: #ECE7DD; font: 1.15rem Georgia, serif;
            margin: .55rem 0; padding: 0; line-height: 1.35; }
        .ads-picker-card p { color: #BDB4A7; font-size: .9rem; line-height: 1.5; }
        .ads-picker-icon { float: right; margin-left: .7rem; }
        .ads-picker-icon img { width: 42px; height: 42px; object-fit: contain; }
        .ads-picker-status { color: #C2B7A9; font-size: .75rem;
            letter-spacing: .06em; text-transform: uppercase; }
        [class*="st-key-panel_card_"] { background: #1A1714; border-radius: 12px; }
        /* Reserve the same space for selected and available cards. The latter
           have no profile controls, but still belong to the same visual grid. */
        [class*="st-key-panel_card_"]:not(.st-key-panel_card_devils_advocate) {
            min-height: 26rem;
        }
        [class*="st-key-panel_select_"] button { min-height: 3rem; }
        [class*="st-key-panel_card_"]:has(.ads-picker-selected) {
            border-color: #A65357 !important; background: #2B1D1D;
        }
        .ads-picker-selected .ads-picker-status { color: #F2D8D5; }
        .st-key-panel_picker button:focus-visible { outline: 2px solid #D2A24C;
            outline-offset: 3px; }
        .ads-picker-included { border-left: 2px solid #D2A24C; padding: .6rem 1rem;
            color: #BDB4A7; margin: .4rem 0 1rem; }
        .ads-picker-included strong { color: #ECE7DD; }
        @media (max-width: 640px) {
            .ads-picker-card { min-height: 0; }
            [class*="st-key-panel_card_"]:not(.st-key-panel_card_devils_advocate) {
                min-height: 0;
            }
        }
        </style>
        <div class="ads-picker-eyebrow">Build your defense panel</div>
        """, unsafe_allow_html=True)
    st.subheader("Choose your panel")
    st.write("Choose 1-3 panelists to focus your practice. Your selection shapes the questions you face.")
    count_col, reset_col = st.columns([2, 1])
    with count_col:
        st.markdown(f"**{len(selected)} of 3 selected** · + Devil's Advocate")
    with reset_col:
        st.button("Use recommended panel", key="panel_picker_reset", width="stretch",
                  on_click=_restore_recommended, args=(roster,))
    if removed:
        names = ", ".join(ARCHETYPE_CONFIG[key]["archetype_title"] for key in removed)
        st.info(f"Your defense type changed. Removed roles unavailable for this type: {names}. Your other choices are kept.")
    if len(selected) == 3:
        st.caption("Panel full. Remove a selected panelist to choose someone else.")
    elif not selected:
        st.warning("Select at least one panelist to convene your panel.")

    for offset in range(0, len(roster), 2):
        for column, key in zip(st.columns(2, gap="small"), roster[offset:offset + 2]):
            title = ARCHETYPE_CONFIG[key]["archetype_title"]
            chosen = key in selected
            icon_html = _icon_html(key)
            with column, st.container(border=True, height="stretch", key=f"panel_card_{key}"):
                status = "Selected" if chosen else "Available"
                selected_class = " ads-picker-selected" if chosen else ""
                st.markdown(
                    f'<div class="ads-picker-card{selected_class}">'
                    f'<span class="ads-picker-icon">{icon_html}</span>'
                    f'<span class="ads-picker-status">{status}</span>'
                    f'<h4>{html.escape(title)}</h4>'
                    f'<p>{html.escape(ROLE_DESCRIPTIONS[key])}</p></div>',
                    unsafe_allow_html=True,
                )
                st.button(
                    f"Remove {title}" if chosen else f"Select {title}",
                    key=f"panel_select_{key}",
                    type="primary" if chosen else "secondary",
                    disabled=not chosen and len(selected) >= 3,
                    width="stretch", on_click=_toggle_panelist, args=(key,),
                )
                if chosen:
                    _render_profile_controls(key)

    with st.container(border=True, key="panel_card_devils_advocate"):
        st.markdown(
            f'<div class="ads-picker-included"><span class="ads-picker-icon">{_icon_html(DEVILS_ADVOCATE_KEY)}</span>'
            '<strong>Devil\'s Advocate · Always included</strong>'
            '<br>Challenges your strongest earlier claim and tests how well you defend it.'
            ' This extra panelist does not use one of your three selections.</div>',
            unsafe_allow_html=True,
        )
        _render_profile_controls(DEVILS_ADVOCATE_KEY)
    st.caption("Choose Statistical & Data Analysis or Results & Conclusions only if your document includes those sections.")
    return selected
