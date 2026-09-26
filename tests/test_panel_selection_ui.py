"""Exercise real Streamlit reruns for the panel-selection interaction."""

from streamlit.testing.v1 import AppTest


def _picker():
    return AppTest.from_string('''
import streamlit as st
from academic_defense_simulator.panel import PANEL_COMPOSITION
from academic_defense_simulator.panel_selection_ui import render_panel_selection
kind = st.selectbox("Defense type", ["thesis", "capstone", "other/certification_interview"])
with st.container(border=True, key="panel_picker"):
    selected = render_panel_selection(PANEL_COMPOSITION[kind])
st.button("Convene", disabled=not selected)
st.json(selected)
''').run()


def test_limit_remove_replace_and_restore():
    app = _picker()
    assert not app.exception
    initial = list(app.session_state['panel_picker_selection'])
    assert len(initial) == 3
    available = next(b for b in app.button if b.label.startswith('Select '))
    assert available.disabled
    app.button(key=f'panel_select_{initial[0]}').click().run()
    assert len(app.session_state['panel_picker_selection']) == 2
    assert not app.button(key=available.key).disabled
    app.button(key=available.key).click().run()
    assert len(app.session_state['panel_picker_selection']) == 3
    app.button(key='panel_picker_reset').click().run()
    assert app.session_state['panel_picker_selection'] == initial
    assert not app.exception


def test_empty_selection_persists_and_blocks_start():
    app = _picker()
    for key in list(app.session_state['panel_picker_selection']):
        app.button(key=f'panel_select_{key}').click().run()
    app.run()
    assert app.session_state['panel_picker_selection'] == []
    assert next(b for b in app.button if b.label == 'Convene').disabled
    assert app.warning


def test_type_change_keeps_only_valid_choices():
    from academic_defense_simulator.panel import PANEL_COMPOSITION

    app = _picker()
    before = list(app.session_state['panel_picker_selection'])
    app.selectbox[0].select('other/certification_interview').run()
    expected = [key for key in PANEL_COMPOSITION['other/certification_interview'] if key in before]
    assert app.session_state['panel_picker_selection'] == expected
    assert app.info
    app.run()
    assert app.session_state['panel_picker_selection'] == expected
    assert not app.exception


def test_card_customization_survives_reselection_and_includes_advocate():
    from academic_defense_simulator.panel import list_icon_choices

    app = _picker()
    key = 'methodology_expert'
    app.text_input(key=f'panelist_name_{key}').set_value('Santos').run()
    choice = next(icon for icon in list_icon_choices()
                  if not app.button(key=f'pick_{key}_{icon}').disabled)
    app.button(key=f'pick_{key}_{choice}').click().run()
    assert app.session_state[f'panelist_icon_{key}'] == choice
    app.button(key=f'panel_select_{key}').click().run()
    assert not any(field.key == f'panelist_name_{key}' for field in app.text_input)
    app.button(key=f'panel_select_{key}').click().run()
    assert app.text_input(key=f'panelist_name_{key}').value == 'Santos'
    assert app.button(key=f'pick_{key}_{choice}').disabled
    app.text_input(key='panelist_name_devils_advocate').set_value('Rivera').run()
    assert app.text_input(key='panelist_name_devils_advocate').value == 'Rivera'
    assert not app.exception
