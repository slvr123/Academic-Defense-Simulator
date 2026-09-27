"""Source context shared by the active defense and transcript history."""
import streamlit as st


def render_question_source(turn) -> None:
    pages = ", ".join(map(str, turn.source_pages))
    noun = "page" if len(turn.source_pages) == 1 else "pages"
    label = f"Source passage - PDF {noun} {pages}" if pages else "Source passage"
    with st.expander(label, expanded=False):
        if turn.source_pages:
            st.caption("Page numbers count from the first page of your PDF, including its cover.")
        else:
            st.caption("Page information was not saved with this older session.")
        if turn.grounding_flagged:
            st.warning("The quoted reference could not be verified in this passage. Check the question against your document.")
        st.caption("Document passage provided to the panelist. This is context, not proof that every claim in the question is correct.")
        st.text(turn.chunk_text)
