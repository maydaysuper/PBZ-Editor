# Validation scope

[简体中文](validation.zh-CN.md)

This release targets Steam build **26266538**. Tests used isolated copies. No player saves, raw runtime reports, sample values, private screenshots or generated game assets are published.

| Area | Evidence and boundary |
| --- | --- |
| Automated tests | Synthetic saves cover read/write boundaries, backups, external changes, untouched slots, UI behavior and language switching; they run without game resources |
| Local game resources | 844 catalog entries, 799 editable entries and 714 item icons; compared against the local baseline using installed game text and map data |
| File roundtrip | Disposable copies passed edits, export, backup, restore and independent decoder checks; unrelated JSON and untouched slots were checked |
| Game readback | Five sample categories (item, main weapon, phantom weapon, accessory, medicine) passed load, manual save, exit and readback; original progress hashes and timestamps stayed unchanged |
| Packaged app | Locally checked fresh data, Chinese/space paths, minimal PATH, portable mode, migration, subsequent launch, backup-preserving upgrades and error handling |
| Clean computer | No second Windows PC or clean VM was available; not tested |
| GitHub build | The workflow is manually triggered. See Actions for its result; CI without the game is not in-game validation |

## Known limitations

- Medicine uses follow equipment and refill rules. A previous load/save refilled 1 use to 2. The successful roundtrip verified 2 uses in an equipped slot; arbitrary counts are not guaranteed to remain unchanged.
- Full outfits update the account-wide wardrobe. Internal hair and body meshes are not unlockable outfits. Outfit appearance has not been verified item by item.
- Catalog and save-schema support do not verify every item's effects, quests, achievements or acquisition flow. Adding a quest item does not replay quest events.
- Some map relationships come from the game's debug region lists and establish association only. Missing relationships remain unassigned.
- Missing English names, descriptions or map labels retain the original name or ID rather than an invented translation.
- This unsigned app may show a Windows publisher prompt. Use this repository's Release and verify SHA256SUMS.txt. Disabling system protection is not required.

`release-manifest.json` records dependency, decoder and executable fingerprints. A local build marks five-sample game readback as passed only when private validation fingerprints exactly match the save implementation and decoder. Third-party or CI builds default to `NOT_TESTED`. Raw validation records are never bundled.
