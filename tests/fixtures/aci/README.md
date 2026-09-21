# ACI fixtures — real, anonymized APIC responses

Supplied by the reporter of [Fabric-AIops #1](https://github.com/AIops-tools/Fabric-AIops/issues/1)
from a production APIC (read-only queries, 2026-09-20). They are a curated,
anonymized batch, **not** untouched captures — read `manifest.json` before
relying on a count or on an attribute being absent:

- `totalCount` is the value APIC reported for the original query, not the
  number of objects in the file (the contract file says 31 and carries 3).
- Several files are excerpts; the manifest lists what was omitted.
- `06-filter-entry.json` is entries only — the `vzFilter` object itself was
  not captured.

One change was made here, and only for this repository's brand-isolation rule:
in `07-faults-active-excerpt.json` the VMM provider and controller tokens in the
two fault DNs were renamed (`prov-VMM-A`, `vmmctrlr-A`, `nic3`). Nothing in the
code reads the provider name — only the `comp/` prefix that marks a VMM-tree
fault — so the substitution changes no behaviour under test.
