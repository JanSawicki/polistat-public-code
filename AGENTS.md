# Instructions for coding agents

Read the root [README.md](README.md) and the README for the specific area before changing code.
This repository is the public code mirror; the linked `polistat-dataset` repository is the source
for the raw and harmonized police data. The BDL experiment is self-contained here and uses only
the cached public inputs under `benchmark/domain-panel-comparison/input/`.

## Analysis rules

- Keep signal detection, panel synchrony, and provenance attribution distinct. A synchronized
  break is a candidate for source review; do not call it an administrative artifact without
  independent documentary evidence matching date and scope.
- Do not treat unlabeled years in real panels as true negatives or report exhaustive real-data
  FPR/FNR when the benchmark labels are incomplete.
- Preserve the frozen thresholds, random seeds, matching tolerance, permutation count, and FDR
  settings in the benchmark configuration. Record any deliberate change in the relevant README
  and result metadata.
- Treat source crosswalks and curated benchmark labels as evidence-bearing data. Do not silently
  regenerate, overwrite, or broaden them from detector outputs; candidate gazette records require
  human source review before becoming benchmark labels.
- Prefer the self-contained synthetic and BDL commands in the root README when private police
  aggregates are unavailable. Full police-data analyses require the companion dataset repository.

## Repository hygiene

- Keep downloaded/private working data and generated temporary artifacts out of this code repo.
  Commit only public source extracts that are explicitly part of a reproducible benchmark.
- Preserve source citations and the originating institutions' terms for downloaded documents.
- Keep user-facing explanations plain and explicit about uncertainty and the limits of the
  benchmark.
