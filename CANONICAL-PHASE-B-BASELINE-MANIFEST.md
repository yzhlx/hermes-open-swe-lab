# Canonical Phase B Baseline MANIFEST

## Immutable baseline facts (pre-commit, tracked)
- old base branch: phase-1-smoke @ 9d1d6356b039f0cc29278781486ddfb2831df8e4  (HISTORICAL — superseded by CI Bootstrap base drift)
- new base branch: phase-1-smoke @ 87073d31957dafeadac6c2ab727a41b93f5f459c  (CI Bootstrap merged via PR #11; authoritative base tip)
- CI Bootstrap PR: #11
- CI Bootstrap head: 831095d0d69870538bef3d53b0535802c21c3746
- CI Bootstrap merge commit: 87073d31957dafeadac6c2ab727a41b93f5f459c
- CI Bootstrap workflow blob SHA: eeef172e08375dc9b1b2070ea40e878ada92d299
- CI Bootstrap Action pins (40-hex, supply-chain frozen):
  - actions/checkout@11d5960a326750d5838078e36cf38b85af677262
  - actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065

## Canonical candidate & source provenance (immutable)
- tested candidate: 0c60d3b427c16b440ebaae00c2ff1dd616ce2dc1
- PR #5 head: 309c6f108a50890955f787591dd09f1304bc5da7 (runtime code SHA 932dcd7ef9d584955d316a3ddfca25c69f7dd6e3)
- PR #6 head: 8806010ca3291be79d886ba5b70c946c19585299 (docs-only checkpoint, no code change)
- PR #7 head: ad3b07a8d9d2e444e2832a241167afaafae06c83 (audit docs ABSORBED into candidate; source PR NOT directly merged)
- PR #8 head: db1b3dbd5374151768e778c86cee3c782ea317ef (5 docs/architecture/*.md imported via blob — five-architecture-doc blob source)
- four-file resolution source: candidate 0c60d3b (constants / github_app / github_client / db)

## Superseded / historical evidence (NOT valid for current head)
- OLD Review id 4782086925: anchored to head 35d538e5f02021cde3dd0e39433a4bd16ffcacfe and old phase-1-smoke base 9d1d6356. STATUS: HISTORICAL / SUPERSEDED. It is not an approval of the current head.
- OLD pytest 178/178 evidence (candidate 0c60d3b; R7 final-branch rerun @ b191209138afc3f0f6f034f1a8569df10afe3df9): STATUS: HISTORICAL / SUPERSEDED. Must NOT be reused as evidence for the current head.

## Semantic equivalence declaration
Authoritative final head, pytest result, CI run and Review ID are recorded
in the external freeze evidence record keyed by the immutable PR head SHA.
They are intentionally not self-recorded in this tracked commit.

## Explicitly NOT recorded here (to avoid self-reference deadlock)
- This MANIFEST commit SHA
- future final canonical head (produced by M7)
- future CI run ID (produced by M12)
- future Review ID (produced by M15)
- not-yet-run new pytest result (produced by M10)
