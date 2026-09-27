# Retrieval and source evidence

## Decision

I added source passages and physical PDF page references to the current question and exchange history.
I preserve those references through follow-ups, Devil's Advocate questions, and saved sessions.
I show an explicit missing-page message for older sessions rather than guessing a location.
I keep document text out of the shareable Markdown transcript; it includes page references only.

I replaced the test that depended on a private local session with a committed synthetic legacy fixture.
I regenerated the bundled sample manifest using the existing builder, preserving its assessment without an API call.
Its passage text and embeddings did not change.

## Offline comparison

I fixed eight diagnostic questions and short evidence anchors before comparing retrieval variants on the two existing evaluation PDFs.
I recorded document hashes so a later run cannot silently evaluate different files.
I used the locally cached all-MiniLM-L6-v2 encoder with a 256-token input limit.
I did not send either document to an external provider.

| Variant | Evidence first | Evidence in top three | Truncated embedding inputs |
|---|---:|---:|---:|
| Current character chunks | 4/8 | 5/8 | 135/141 chunks |
| Page-bounded token windows | 3/8 | 5/8 | 0/507 windows |
| Mean-pooled windows per page | 4/8 | 5/8 | 0/507 windows |

I tested 32-token overlap with a content budget of 254 tokens plus two special tokens.
I recheck each sliced passage with the tokenizer to account for word fragments at boundaries.
The pooled candidate averages normalized window embeddings and normalizes the resulting page vector.
Its retained full-page context can exceed 256 tokens, but each actual embedding input fits.
The result artifact's `max_tokens` measures retained passage length, not the pooled candidate's embedding-window length.

I kept the current chunker as the application default because neither candidate improved aggregate retrieval and both lost previously successful individual cases.
The truncation problem therefore remains in the default path; the tokenizer-aware alternative is implemented for evaluation, not enabled in production.
I verified that adding page provenance leaves the current chunk texts identical on all three available documents.

## Limits and next decision

I treat these results as a small diagnostic comparison, not a general accuracy estimate.
The labels look for a particular evidence span, so other valid passages may be counted as misses.
I tested document-specific queries, while the defense engine currently searches using broader panelist-focus queries.
I have not evaluated generated-question quality or scoring changes in this work.

My next retrieval step is to expand and review the labels, include the actual panelist queries, and test small retrieval windows that return a larger surrounding passage.
I will require a broader comparison before replacing the default.

## Reproduction

I keep the original evaluation PDFs local and excluded from Git.
The benchmark requires the exact files identified in `evals/retrieval_cases.json` plus a locally cached embedding model for offline execution.
From the repository root in PowerShell:

```powershell
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
.venv/Scripts/python.exe scripts/benchmark_retrieval.py
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

I can supply another document directory with `--documents-dir` and another result path with `--output`.
Results are in `evals/retrieval_results.json`; the script does not write full source passages into that artifact.
