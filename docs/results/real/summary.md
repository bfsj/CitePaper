# Retrieval evaluation

Corpus: four-real-papers-manual-regression; cases: 12; budget: 400 retrieval units.

| Strategy | Context | Full span recall | Mean units | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| topk | True | 0.833 | 397.0 | 62.58 | 73.41 |
| neighbors | True | 0.750 | 396.8 | 64.59 | 86.32 |
| rse | True | 0.583 | 396.8 | 99.94 | 120.12 |
| rse | False | 0.750 | 395.5 | 96.81 | 115.79 |

- Small manually selected retrieval regression, not an unbiased benchmark.
- Budget units are not model tokenizer tokens.
- No LLM answer quality or semantic entailment measured.
- RSE is not guaranteed to beat simpler baselines.
