# CitePaper research report

```text
retrieval evidence budget and limitations
```

Mode: extractive (no LLM).
Citation checks validate source spans, NOT semantic truth. Human review required.

## Observation 1 (other)

```text
# Method
Atlas uses continuous evidence segments for retrieval. The retriever ranks individual chunks with BM25 before expanding adjacent chunks. The next paragraph contains the budget constraint, not a separate method.

Expansion stops at the document boundary. Context is limited to 240 retrieval units. Source quotations are copied from the original page without adding generated context.
```

Evidence: Ec0a1125936ab

## Observation 2 (other)

```text
# Experiments
This is a fictional test document, not a published experiment. Atlas is tested on twelve synthetic questions with a fixed context budget. The evaluation compares isolated chunks, fixed neighbors, and continuous segments.
```

Evidence: Ef94a801ab094

## Observation 3 (other)

```text
# Limitations
Atlas has no OCR support. Its parser may reorder two-column PDF text. It does not verify semantic entailment of generated claims.
```

Evidence: Eb75b61674bbb

## Observation 4 (other)

```text
# Method
Cedar links claims to immutable source snapshots using evidence identifiers. Each evidence record includes the paper hash, page number, and character offsets. The verifier reconstructs the quoted span from the stored page text.

An exact quote match verifies provenance, not whether the claim follows from it. Semantic entailment is not evaluated automatically in this fictional study.
```

Evidence: E145e4d833020

## Evidence ledger

### Ec0a1125936ab | page 1 | chars 0:391

```text
{
  "title": "SYNTHETIC Atlas retrieval study",
  "source": "synthetic://citepaper-fixture",
  "section": "Method",
  "document_hash": "79220a3e7127afe99add33893a12751f3a13f452cb312175041b3ea8ac55fbbd",
  "quote": "# Method\nAtlas uses continuous evidence segments for retrieval. The retriever ranks individual chunks with BM25 before expanding adjacent chunks. The next paragraph contains the budget constraint, not a separate method.\n\nExpansion stops at the document boundary. Context is limited to 240 retrieval units. Source quotations are copied from the original page without adding generated context."
}
```

### Ef94a801ab094 | page 2 | chars 0:234

```text
{
  "title": "SYNTHETIC Atlas retrieval study",
  "source": "synthetic://citepaper-fixture",
  "section": "Experiments",
  "document_hash": "79220a3e7127afe99add33893a12751f3a13f452cb312175041b3ea8ac55fbbd",
  "quote": "# Experiments\nThis is a fictional test document, not a published experiment. Atlas is tested on twelve synthetic questions with a fixed context budget. The evaluation compares isolated chunks, fixed neighbors, and continuous segments."
}
```

### Eb75b61674bbb | page 2 | chars 235:378

```text
{
  "title": "SYNTHETIC Atlas retrieval study",
  "source": "synthetic://citepaper-fixture",
  "section": "Limitations",
  "document_hash": "79220a3e7127afe99add33893a12751f3a13f452cb312175041b3ea8ac55fbbd",
  "quote": "# Limitations\nAtlas has no OCR support. Its parser may reorder two-column PDF text. It does not verify semantic entailment of generated claims."
}
```

### E145e4d833020 | page 1 | chars 0:394

```text
{
  "title": "SYNTHETIC Cedar citation study",
  "source": "synthetic://citepaper-fixture",
  "section": "Method",
  "document_hash": "c8f37cb07f128acf2b3f98ea86bfd8f2b87880ed26b843041769202a2412f353",
  "quote": "# Method\nCedar links claims to immutable source snapshots using evidence identifiers. Each evidence record includes the paper hash, page number, and character offsets. The verifier reconstructs the quoted span from the stored page text.\n\nAn exact quote match verifies provenance, not whether the claim follows from it. Semantic entailment is not evaluated automatically in this fictional study."
}
```

## Evidence gaps

```text
Extractive mode: excerpts only; no model-written comparative analysis.
```

## Verification

```text
{
  "source_spans_valid": true,
  "invalid_evidence": [],
  "citation_links_valid": true,
  "problems": [],
  "semantic_entailment": "not_evaluated",
  "needs_review": false
}
```