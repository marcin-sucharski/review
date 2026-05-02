# Known Limitations

There are no open known limitations against the current documented local review workflow.

The implementation uses Pygments for syntax highlighting, supports mouse drag range selection when the terminal reports drag events, supports editing and deleting saved comments through command mode, and archives completed non-empty reviews as JSON.

Draft or interrupted review sessions are not persisted; only completed reviews with at least one saved comment are archived.
