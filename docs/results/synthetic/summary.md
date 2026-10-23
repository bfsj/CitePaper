# Retrieval evaluation

Corpus: synthetic-regression; cases: 12; budget: 240 retrieval units.

| Strategy | Context | Full span recall | Mean units | p50 ms | p95 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| topk | True | 1.000 | 144.2 | 4.02 | 4.84 |
| neighbors | True | 1.000 | 144.2 | 4.04 | 5.29 |
| rse | True | 1.000 | 114.1 | 4.12 | 5.00 |
| rse | False | 1.000 | 93.7 | 4.27 | 5.14 |

- Small manually selected retrieval regression, not an unbiased benchmark.
- Budget units are not model tokenizer tokens.
- No LLM answer quality or semantic entailment measured.
- RSE is not guaranteed to beat simpler baselines.
