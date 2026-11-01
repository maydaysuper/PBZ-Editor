# PBZ Editor

[简体中文](README.md) | English

An offline Windows save editor for **Phantom Blade Zero**. Find items by name or map, browse game icons, edit your backpack, and keep automatic backups before saving.

**[Download for Windows](https://github.com/qry6868/PBZ-Editor/releases/tag/v0.2.0)** · [Release notes](docs/releases/0.2.0.en.md) · [Validation scope](docs/validation.en.md)

## Get started

1. Download `PBZ-Editor-0.2.0-windows-x64.zip` and extract the entire archive into a folder.
2. Exit the game, then run `PBZ-Editor.exe`. No Python installation or manual DLL setup is needed.
3. The editor detects the game and save. Choose the game root if detection fails, or select your save when multiple accounts are found.
4. Search or filter by category and map, choose **Add to backpack**, adjust values on the right, then click **Save**.

Switch between **简体中文 / English** at the top without losing pending edits. The first launch follows your system language; later launches remember your choice. English item names, descriptions and map labels come from your installed game. Missing translations retain their original names or internal IDs.

The first launch reads the installed game to prepare and cache item metadata and icons. The download includes no player saves, game images or full game text. Game installation files are not modified. The GitHub `Source code` downloads are for developers; players should choose `windows-x64.zip`.

## Saving and recovery

Item selection and value changes update an in-memory backpack preview. Only saving writes those edits. Undo, export to a new copy, and backup restore are available. Before saving, the editor checks whether the game is running or the file changed externally, creates a backup, validates the result, and replaces the file. Untouched slots retain their original ciphertext.

Settings, caches and backups use `%LOCALAPPDATA%\PBZ-Editor` by default. Extract a new version to upgrade. For portable mode, create an empty `portable.flag` beside the EXE; data uses the adjacent `.local` folder, which must be kept during upgrades. Keep the complete application folder together, not just the EXE.

## Compatibility

**v0.2.0 preview** targets Windows x64 and Steam build **26266538**. Writes are refused for unsupported builds. Other game builds and a clean Windows PC have not been validated.

The supported build produces 844 catalog entries, including 799 editable entries across main weapons, phantom weapons, accessories, items/materials, medicines and full outfits. The remaining 45 internal appearance components are browse-only. Map associations come from game resources; some do not establish the first or only acquisition location.

Five sample categories passed a game load, manual save, exit and file readback. This does not verify every item's display, effects or quest state. The game may refill medicine uses; full outfits affect the account wardrobe. See [validation and limitations](docs/validation.en.md).

## Development and feedback

Project code is MIT licensed. See [third-party notices](THIRD_PARTY_NOTICES.md) for bundled components. This community tool is not affiliated with the game developer.

[Build from source](docs/building.en.md) · [Report an issue](https://github.com/qry6868/PBZ-Editor/issues/new/choose)

Include the editor version, game build, steps and error text. Do not attach saves, account folders or complete private logs to public issues.
