# How automatic compatibility works

[简体中文](compatibility.zh-CN.md)

There is no version selector. Each time a save opens, the editor checks decryption and complete parsing, then checks the item data and save fields needed by each category adapter.

| Change | Behavior |
| --- | --- |
| EXE differs from the reference | Locate candidate keys and authenticate them against the save directory; never execute the EXE or reject solely by build number |
| Key material moves or changes | Perform a bounded search inside recognized PE sections; accept only an authenticated key |
| Item values, text or entry counts change | Check property layouts, row counts, IDs and required metadata; use current values when checks pass |
| One category changes layout or loses resources | Stop editing that category while retaining other validated categories |
| Map parsing fails | Disable map filtering; retain search and category browsing |
| Text or some icons are unavailable | Keep original names or IDs; icon failure does not block editing |
| A container version changes but the old layout still parses completely | Read-only; disable edits, writeback and backup restore |
| Decryption or complete parsing fails | Explain the failure and stop without writing |
| Game files update after opening | Block writing until the save is reopened and checked again |

Saving still requires operation validation, full candidate readback, untouched-slot protection, external source-change checks and an automatic backup. Advanced ID entry and export obey the same category restrictions.

“Format checks passed” does not verify every in-game effect. An update might keep field names but change level or quest rules. Untested game versions are identified in the UI. Steam build 26266538 remains the in-game reference environment.

New adapter rules need actual field-semantic checks and game readback, not just a replaced fingerprint. Configuration contains resource fingerprints, property indices and key-location references, never keys or player data.
