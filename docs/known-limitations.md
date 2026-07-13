# Known Limitations

The implementation uses explicitly selected Arborium tree-sitter grammars, supports mouse drag range selection when the terminal reports drag events, supports editing and deleting saved comments, and archives completed non-empty reviews as JSON.

Current limitations are:

- Git paths are represented as UTF-8 strings. Spaces, tabs, newlines, and Unicode names are supported, but filenames containing invalid UTF-8 bytes are not preserved losslessly.
- Binary files and Git links are represented by change metadata rather than inline content.
- Draft or interrupted review sessions are not persisted; only completed reviews with at least one saved comment are archived.
