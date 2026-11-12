# Preserved Fermat research

These documents and `tools/continuous_fermat` and `tools/continuous_path_lab`
come from original commit `97b65b57367dce2f1b11434ef96543bb51452703`.
The imported files retain their original bytes. Their measurements describe
that research version, not the canonical native continuous-extrusion planner.

The Fermat production planner and strict exporter remain available at
`archive/2026-10-09/fermat/heads/codex/continuous-slicing-safety-hardening`.
Its mandatory fixed 20-model gate failed historically: 9/20 fully certified
models and 443 failing layers. Archival preservation and importing standalone
tools do not promote that planner or relax its certification requirements.
The historical no-commit/no-push instruction in the development audit applies
to certification of that research; Leo explicitly authorized this consolidation
and archival preservation in the consolidation handoff.

The standalone Python auditor requires Fermat section markers and its strict
firmware state. Use `scripts/check_continuous_gcode.py` and
`scripts/check_continuous_crossings.py` for canonical native exports. A pass
from one exporter contract does not certify output from the other.

Run the preserved independent tool tests from the repository root:

```powershell
python -m unittest tools.continuous_fermat.test_validate_gcode
# Corpus geometry tests require the corpus requirements in an isolated environment.
$env:PYTHONPATH = 'tools\continuous_fermat\corpus'
python -m unittest tools.continuous_fermat.corpus.test_build_corpus
```

The path lab is a research visualization. Its displayed metrics do not replace
the production planner's geometry or serialized-output checks. Original model
licenses, provenance, corpus meshes, results, settings, and runtime artifacts
remain in the local consolidation data archive; private profiles are not
published in Git.
