# Delivery checkpoint, 2026-11-12

## Repository finalization scope

The intended integration target is `continuous-extrusion`, not `main`.
Finalization merges the standalone tools, launcher and preservation documentation
from PR #2 into that existing native baseline. It does not promote the native
executable to a fully qualified release or change the repository default.

`.github/workflows/consolidation.yml` runs on the exact PR head and on pushes to
the integration target, so the resulting merge commit receives its own CI.
Linux and Windows run the native Python auditor/preview tests, Fermat G-code
auditor tests, corpus section geometry tests, and Python syntax checks. Windows
also exercises the real launcher with disposable paths and a mocked process
start, covering missing inputs and default/explicit projects with spaces.
The workflow rejects changes to the native source/tests/resources/dependency
trees relative to `36c5154d53e8d25639265b3f43be54fb7a688b85`: these checks qualify
the consolidation scope only. Native changes require platform qualification.

The observations below were taken before this finalization workflow was added;
in particular, the initial absence of PR checks is not a passing CI result.
Exact head/merge check receipts are available in PR #2 and GitHub Actions.

## Canonical source and Windows package

Use `C:\Users\Leo\Code Projects\OrcaSlicer-2.4.0` and
`https://github.com/LeoHougaard/OrcaSlicer`.
The checked source checkpoint is `fd7c891e5e0b11ffe6b8dc301319ea06b4d96fd7`
on `codex/orcaslicer-consolidation`. This document is a subsequent documentation
change. Native application code, tests, resources and workflows have no changes
from baseline `36c5154d53e8d25639265b3f43be54fb7a688b85`.
The legacy `orcaslicer-src` is provenance and parked research only.

[Draft PR #2](https://github.com/LeoHougaard/OrcaSlicer/pull/2) remains open,
targeting `continuous-extrusion`. The remote source checkpoint matched the local
checkout before this documentation update. The repository default remains `main`.
No merge or default-branch promotion was performed.

Launch the canonical build using `scripts/open_canonical_orca.ps1`, which uses
`build-continuous/src/Release/orca-slicer.exe`, the adjacent private
`OrcaSlicer-data/userdata`, and `OrcaSlicer-data/projects/Continuous Infill.3mf`.
The installed slicer is separate. This check did not launch or update either app.

The existing standalone package is
`build-continuous/readiness/OrcaSlicer-Continuous-Windows-x64.zip`.
It contains the native baseline above, runtime libraries, resources, documentation
and checksums, without personal profiles or models.

| Identity | Value |
| --- | --- |
| ZIP bytes | `174510960` |
| ZIP SHA-256 | `923359e073cf92f4a4248b692726ad34b600d9508d6b8b42e25c16565b7ed819` |
| Packaged and canonical executable SHA-256 | `6c84256590cdf64463b00f6e01dd5f7e97783d6cea2aee86219018932d707c70` |
| Packaged file-manifest entries checked today | `14985`, all match |

The ZIP sidecar checksum matches. Reading and hashing every manifest entry also
checked the compressed file contents. The package inside the preserved local
`native.zip` has the identical ZIP SHA-256. This is an available Windows testing
package, not a newly qualified cross-platform release.

## Evidence and proportionate checks

The September release record is
`build-continuous/readiness/verification.json` and `RELEASE-READINESS.md`.
The October matched evidence is under the adjacent
`OrcaSlicer-data/consolidation-2026-10-09/verification/native` and `upstream`.
The native `models.log` contains 96 passing case records. The consolidation
record reports both candidates passing 96/96, with worst per-layer missing area
3.1243%, and zero audited same-height crossing/retrace or object travel/retraction.
The October GUI export reports 65 layers and 35,098 object extrusion moves.
These are historical software results, not fresh corpus or physical results.

Fresh lightweight checks today:

```powershell
python -m unittest discover -s scripts -p 'test_continuous*.py'
ctest --test-dir build-continuous -C Release -N -R 'Continuous|continuous'
git diff 36c5154d53e8d25639265b3f43be54fb7a688b85 fd7c891e5e0b11ffe6b8dc301319ea06b4d96fd7 -- src tests resources .github
```

Results: 10 Python tests pass; CTest lists 21 tests without executing them;
the source comparison is empty. The existing generated build has no matching
`continuous` label (`-N -L continuous` lists zero). Use the name filter above
for this saved build; this is not evidence of missing test source registration.
Both C++ test lists include `test_continuous_extrusion.cpp`, and current source
uses Catch tag discovery. No reconfigure, compile or C++ suite was run today.

## Recovery and accessible delivery

The [rollback release](https://github.com/LeoHougaard/OrcaSlicer/releases/tag/consolidation-backup-2026-10-09)
is a published prerelease containing encrypted recovery data, not an ordinary
application installer. All 29 assets remain uploaded. All 26 encrypted part sizes
and GitHub SHA-256 digests match the preserved `backup-manifest.json`. Anonymous
downloads of the manifest, `RESTORE.md` and `restore_backup.py` match local bytes;
an anonymous HEAD request for the first native archive part succeeds.
The large encrypted parts were not downloaded or decrypted again today.

The local recovery key exists at
`C:\Users\Leo\Code Projects\OrcaSlicer-data\orcaslicer-consolidation-recovery-key.txt`.
Its contents were not read or published. An independent copy of that key is
required for recovery after losing this computer; that off-machine copy is not
verified. October evidence records successful metadata decryption and complete
offline Git restore. No fresh full disaster-recovery pass is claimed.

For recovery on a new machine, follow the release's `RESTORE.md` in a new empty
destination. Select `preservation-metadata.zip` for projects/profiles,
`original-histories.bundle` for history, and `native.zip` for native runtime and
evidence. The standalone package is inside `native.zip` at
`build-continuous/readiness/OrcaSlicer-Continuous-Windows-x64.zip`.
After verifying/decrypting with `restore_backup.py`, extract that nested package
into a new folder. Use a separate data directory for any acceptance run.
Restore historical refs in a separate checkout; never bulk-push private refs.
All 137.5 GiB of proposed obsolete copies remain outside this task's cleanup scope.

## Actual remaining Orca gates

The [September platform run](https://github.com/LeoHougaard/OrcaSlicer/actions/runs/35234541588)
is completed with failure, not still running. It tested alternate upstream SHA
`6aa38fa413dbc5eaa279fcf54f3ca369917a15dc`, not the consolidated source checkpoint.
Windows x64 failed at system preset cache generation; Linux x86-64 failed at
external slicer regression tests. macOS x86-64/arm64, macOS universal, Linux
aarch64 and Windows arm64 build jobs succeeded; macOS arm64 unit tests succeeded.
Those successes do not qualify the canonical branch.

PR #2 reports no status checks. `build_all.yml` automatically covers PR targets
`main` and `release/*`, with source path filters; this draft targets
`continuous-extrusion`. A documentation push does not provide platform evidence.
No broad CI dispatch or workflow expansion was made for this checkpoint.

Finite release gates are:

1. Review and promote the canonical draft; establish successful Windows, macOS
   and Linux build/package checks for the exact source being released, including
   applicable preset-cache and regression steps. Diagnose any failures on that
   source rather than treating the alternate branch's old run as current CI.
2. Qualify a candidate's complete GUI/project workflow on its intended target
   systems. Historical Windows GUI evidence exists; clean canonical builds and
   current cross-platform interactive acceptance remain unverified.
3. Physical TPU acceptance: finish, real flow limits, unsupported spans and full
   3D nozzle clearance. Software centerline audits do not establish these.
   No physical print, device control or slicing algorithm work was performed.

Fermat certification remains failed and research-only; paused research is not a
release gate for the chosen native implementation.

## Onshape Slicer Link 0.3.3: read-only handoff

The separate source is `C:\Users\Leo\Code Projects\Onshape Slicer Link`.
Its clean `main` checkout is `da5800f`; application code last changed at
`d09a9f7c32f438415c1031004bf67ac0a8d18362`. The subsequent `a899cd2` records stock
verification and `da5800f` records storage recovery.

The [0.3.3 release](https://github.com/LeoHougaard/onshape-slicer-link/releases/tag/v0.3.3-test)
still provides its Windows installer and Linux archive. Anonymous HEAD requests
to both assets succeed. The release points to `d09a9f7c32f438415c1031004bf67ac0a8d18362`;
[build 35034537169](https://github.com/LeoHougaard/onshape-slicer-link/actions/runs/35034537169)
is completed successfully at that SHA. Windows installer: 19,709,213 bytes,
SHA-256 `2eab510eb1bb28bd16a5b56749923cd0f798307e5f92ba774dede3663307590a`.
Linux archive: 38,527,947 bytes,
SHA-256 `6a3b981ea5301b579a17fd9cc6ebbbcc72525113526fd6dacf627293439cbd67`.
These are current GitHub asset metadata, not fresh binary execution results.

Read `docs/stock-verification.md`, `clean-install-verification.md`, `user-test.md`
and `storage-recovery.md` in that project. The 89 automated Windows checks and
live 12 x 12 x 500 mm export are historical 0.3.3 evidence, not rerun today.

| Acceptance step | Existing evidence and remaining acceptance |
| --- | --- |
| Install/update | Clean Windows VM 0.3.2 setup; exact CI 0.3.3 upgrade exported successfully with saved connection/links. The old local app needed **Connection & API use > Stop local app**, then installer **Try again**. No update or service stop was performed today. |
| Export | Live configured drill-bit export accepted at 12 x 12 x 500 mm; host stock Orca and saved 3MF dimensions matched. The test part exceeds printer height; it was not sliced. |
| Reload | Stock Orca Reload All and Bambu Reload from disk changed asymmetric fixtures from 32 to 46 mm. These are earlier stock checks, not a full 0.3.3 acceptance rerun. |
| Placement/settings | Earlier single-part transforms retained; Bambu two-plate selection retained both placements, rotations, plates, walls and infill. General geometry changes may recenter on native reload. Painted supports/materials require separate review. |
| Save/reopen | Stock saves and source-file mirroring have evidence; clean VM save/rendering failed, so it does not prove reopen. Complete close/reopen, another CAD change, Send/Update and native reload with retained placement/settings remains hands-on acceptance. |

Use the released stock-app route: link part, Send/Update, move/rotate and set
infill, change CAD, Send/Update, select and native reload, then save 3MF, connect
saved project, Send/Update, close/reopen and repeat. Keep slicing and printing
manual. `docs/manual-update.md` describes a separate historical modified-slicer
experiment, not the 0.3.3 stock installation route. Embedded preview remains
unfinished. Linux execution remains unverified and is not expanded here.

Development recovery is documented at tag `storage-archive-2026-10-23`; private
native experiment recovery is separate from public 0.3.3 delivery. No Onshape
source, credentials, account state, installed application or device was modified.
