# Canonical OrcaSlicer project

Use `LeoHougaard/OrcaSlicer` and the local checkout at
`C:\Users\Leo\Code Projects\OrcaSlicer-2.4.0`.
The integration branch is `codex/orcaslicer-consolidation`, based on
`continuous-extrusion` at `36c5154d53e8d25639265b3f43be54fb7a688b85`.
The existing `main` and both forks' default branches are unchanged pending review.

Open `Open Canonical OrcaSlicer.lnk`, or run:

```powershell
.\scripts\open_canonical_orca.ps1
```

The launcher uses the canonical Release application, the preserved editable
`Continuous Infill.3mf`, and a copied local data directory outside the build
tree. Settings and projects live in the adjacent `OrcaSlicer-data` directory.
They remain private. The installed application in `C:\Program Files\OrcaSlicer`
remains available.

## Chosen behavior

The working 2.4 native continuous-extrusion version and the newer-upstream
candidate both passed the same 96-case geometry sweep, density matrix, flow
matrix, and saved-project reload. The 2.4 version also passed a fresh interactive
GUI slice of the preserved user project. Keeping that version retains the
verified editable profiles and avoids taking unrelated upstream changes as part
of consolidation. The newer candidate remains available in its original branch.

This integration adds the independent Fermat G-code auditor, corpus construction
tools, and path lab with their original source bytes. The alternate C++ planner
and strict exporter remain available through preserved Git refs. They are not
enabled in the canonical native application. See
[the preserved research record](research/fermat/README.md) for their contracts.

| Original work | Integration decision |
| --- | --- |
| Native walls, sparse/solid fill, seams, changing sections, standard Klipper export | Retain the verified working implementation unchanged. |
| Newer-upstream continuous extrusion | Preserve exact branch and ancestry; matched behavior passes, but no GUI advantage was established. |
| Fermat production planner and deterministic firmware exporter | Preserve complete research history and corpus; its fixed certification gate remains authoritative. |
| Fermat Python auditor, corpus tools, and path lab | Import byte-identical standalone tools and their documentation. Label them by their own contract. |
| Earlier multi-island continuous-filament loop | Preserve exact history and runtime evidence. Do not silently combine its travel/firmware assumptions with the native route. |
| Staged Z-hop rejection removal and Simple-option visibility edits | Preserve in a separate archival snapshot, with the exact original staged patch. Native object continuity has a different contract. |
| Unstaged Downloads iteration/audit log | Preserve in a separate archival snapshot, with the exact original unstaged patch. |
| Deleted/abandoned experiments and orphan Git objects | Preserve recovered commits under named refs and non-commit objects in the local data archive. |

No slicer source, printer defaults, profile schema, or production certification
threshold changes in this integration. Native ordinary slicing and the native
continuous route use the same source as the chosen baseline.

## Fresh Windows verification

The original baselines were rebuilt using existing build and dependency trees.
Logs and full outputs are under
`OrcaSlicer-data\consolidation-2026-10-09\verification`.

| Check | Working 2.4 | Newer upstream |
| --- | --- | --- |
| Continuous geometry, settings migration, seams and ramps | 24 cases, 290,807 assertions passed | 24 cases, 296,775 assertions passed |
| Native FFF, flow, reslicing and mode-disabled behavior | 6 cases, 57,400 assertions passed | Passed |
| Geometry sweep with fixed coverage/timing/crossing gates | 96/96 passed | 96/96 passed |
| Density/walls/shells/seams and project reload | 10/10 plus reload passed | 10/10 plus reload passed |
| Flow/calibration/cooling/volume/cache controls | 10/10 passed | 10/10 passed |
| 3MF/configuration/preset compatibility | 15 cases, 128 assertions passed | 114 cases, 849 assertions passed |
| Supports, vase, unsupported firmware and raft export | All four rejected | All four rejected |

The sweep uses identical inputs, checks exported G-code independently, and
retains the existing limits: zero crossing/retrace, less than 5% aggregate
missing/excess area, less than 8% missing on every layer, less than 2% outside
area, and less than two minutes per small model. Both candidates' worst missing
layer was 3.1243%. These native targets differ from Fermat certification.

The actual GUI output had 65 layers and 35,098 object extrusion moves. Independent
command and crossing audits found zero object travel/retraction and zero
crossing/retrace conflicts. The inspected GUI shows 15% sparse infill, two walls,
Back seam, the loaded printer/material profiles, and the generated preview.
The app reported approximately 0.9512% estimated missing material. Startup/skirt
travel occurs outside the marked object route.

The native Python auditor/preview tests passed 10 tests. The imported Fermat
auditor and corpus geometry tests passed 30 tests, using the preserved corpus
environment for its declared geometry dependencies.

Native mode still has its documented one-object/material/region limits and may
omit unreachable material when the explicit omission setting is enabled. The
GUI reports that omission. Neither native checks nor the stricter research
checks establish physical printing results. Linux/macOS builds and physical
printer behavior were not verified in this Windows consolidation.

## History, data and restoration

`orcaslicer_consolidation_history.json` maps published original refs to their exact
preserved object hashes. The complete private mapping is in the local data
folder. Archival branches and tags use the prefix
`archive/2026-10-09/`. Original histories are not squashed or rewritten.
Annotated tags retain their original tag objects, including their original
internal names. Remote-only backup branches and pull-request head/merge refs
are included. The former shallow boundaries have complete ancestry.

Private T3 checkpoint refs are preserved in the canonical local repository and
the offline history bundle. A profile scan found nonempty printer API keys in
some checkpoint snapshots. Those checkpoints are excluded from GitHub;
publication uses an explicit reviewed ref list rather than a wildcard. Original
authored branches/tags and the two local-edit snapshots are published with
their exact history. No credential-bearing history is redacted or rewritten.

Verification preserved 611 original refs locally and published 401 original
refs plus two local-edit snapshots. All 403 public refs matched live GitHub
hashes and passed a fresh full-object fetch and integrity check. All 611
original refs passed a separate restore from the private offline bundle into
an empty repository. The 210 private checkpoint refs were not published.
The offline bundle is 1.193 GiB. The five data/runtime archives total 11.936
GiB and contain 249,813 files; every archived file's SHA-256 was verified.

The local preservation folder contains original status/index metadata,
staged/unstaged binary patches, ref/reflog inventories, GitHub repository and
discussion metadata, recovered orphan objects, hash manifests, and runtime/data
archives. Models retain their source/license/provenance records. Archives keep
unique ignored work, models, profiles, logs, scripts, screenshots and runtime
files. Compiler intermediates, generated CMake caches and downloaded dependency
build trees are excluded as documented in `artifact-backup.json`. Originals
remain untouched until cleanup review.

To restore a historical branch without modifying the active checkout:

```powershell
git worktree add ..\OrcaSlicer-history archive/2026-10-09/fermat/heads/codex/continuous-slicing-safety-hardening
```

Create such a worktree only when needed. Restore the corresponding data archive
into that checkout to recover its ignored corpus and artifacts. Dependency/build
caches can be rebuilt from that branch's pinned configuration; do not mix
incompatible toolchains or dependency versions into the canonical build.

The earlier Downloads loop uses the nested source layout `OrcaSlicer-main/src`.
Its archival local-work snapshot retains that layout. To reproduce the original
staged state, check out its original commit and run `git apply --index` on the
saved `loop/staged.patch`. For the aligned worktree's unstaged state, check out
its original commit and run `git apply` on `aligned/unstaged.patch`.
Run these commands in the restored historical checkout, not the canonical one.

## Promotion and cleanup review

The draft PR targets `continuous-extrusion`. After review and merge, the proposal
is to make that branch the canonical default working branch. Original `main`
history remains preserved. Archive
`LeoHougaard/OrcaSlicer-experimental-spiral-hybrid-mode` after verifying its
preserved refs; do not delete it. Archiving retains its original discussions and
pull requests, whose metadata is also captured locally.

Keep `build-continuous` as the canonical build and `deps/build` as its dependency
environment. The exact obsolete local copies, sizes, archive coverage and
removal order are listed in the local `cleanup-review.json`. No deletion or
default-branch promotion is part of this integration.

The measured obsolete copies total 137.485 GiB. Paths below `canonical` resolve
against `C:\Users\Leo\Code Projects\OrcaSlicer-2.4.0`; the local JSON records
the complete absolute paths. Remove linked worktrees through Git before
removing their owning repository.

| Obsolete copy | GiB |
| --- | ---: |
| `C:\Users\Leo\Downloads\OrcaSlicer-github-aligned` | 24.631 |
| `C:\Users\Leo\Downloads\OrcaSlicer-main` | 10.832 |
| `C:\Users\Leo\Code Projects\orcaslicer-src` | 52.514 |
| `canonical\sandboxes\continuous-upstream` | 0.512 |
| `canonical\build` | 26.504 |
| `canonical\build-continuous\upstream` | 19.055 |
| `canonical\build-continuous\upstream-deps` | 0.704 |
| `canonical\build-continuous\upstream-extra-deps` | 0.220 |
| `canonical\build-continuous\upstream-extra` | 0.042 |
| `canonical\build-continuous\src\Production` | 0.393 |
| `canonical\build-continuous\src\Generalized` | 0.360 |
| `canonical\build-continuous\src\Overlap` | 0.360 |
| `canonical\build-continuous\src\Integrated` | 0.360 |
| `canonical\build-continuous\src\Infill` | 0.360 |
| `canonical\build-continuous\src\Verification` | 0.274 |
| `canonical\build-continuous\readiness\OrcaSlicer-Continuous` | 0.364 |
