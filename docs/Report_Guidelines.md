# Report guidance

The maintained model and ETL description are in [README](../README.MD); current results are in [Data Analysis](Data_Analysis.md).

Use all three facts: title releases, reconstructed annual series interval information, and current title ratings. Share year, type, genre combination and audience dimensions. The rating fact has title_id as its sole primary key; the download date and file hash belong to ETL provenance, not an extraction dimension. Individual genres use the shared bridge.

State that title.basics.tsv and title.ratings.tsv were downloaded from IMDb on **12/11/2026**, as supplied by the author. Cite the official download URLs and preserve hashes and sizes. The previous professor-provided data.tsv is a reference, not the active default input.

Keep known intervals and unknown-end counts separate; neither establishes actual airing. Distinguish reconstructed interval snapshots from ratings observed in an extract. Never sum accumulated votes across extracts, convert missing ratings to zero, or describe release-cohort ratings as historical opinions. Show coverage and sample sizes beside rating means; genre groups overlap. Ranking thresholds are project choices, not official IMDb rankings.

Regenerate analysis_results.json, the eight figures and Data_Analysis.md after a successful warehouse rebuild. Discuss measured results and limitations rather than reusing statistics from the previous extract. Schema version is 4; series semantics version remains 2. Tests cover invalid/duplicate ratings, unmatched titles, coverage denominators, bridge multiplicity and publication failure recovery.

For the model diagram, show attributes, PK/FK/UK flags, composite keys and physical `1 : 0..N` cardinalities. Regenerate Mermaid and optional Graphviz SVG/PDF exports using `python -m scripts.model_diagram`; do not draw a physical FK between release and rating facts.

Before generating the analysis report, run `python -m scripts.runtime_diagnostics`. Explain the verified 2000s source outlier, report median/P95/P99 and coverage, and label exclusions as sensitivity scenarios rather than corrections. Figure 1 must show the unmodified mean without clipping; Figure 3 uses equal-scale 2D panels and an annual-count denominator. The conclusion should answer the initial release/genre, runtime, series-interval and ratings questions with measured quantities and their limits.
