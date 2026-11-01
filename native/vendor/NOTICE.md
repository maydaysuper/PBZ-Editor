# oozextract

Upstream: https://github.com/lvlvllvlvllvlvl/oozextract
Version: 0.5.7, obtained from the published crates.io package.
License declared by upstream Cargo.toml: MIT.

The source is retained in `oozextract/` for reproducibility. The sole local
change moves `test-log` from normal dependencies to dev-dependencies: its
uses are inside upstream's `cfg(test)` module. Decoder implementation is
unchanged. Original package/VCS metadata remains alongside the source.
