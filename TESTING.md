# Testing

From the repository root, run:

```sh
python -I -m unittest discover -s tests -v
python -I examples/run_examples.py
```

Local run on 2026-11-12 (Asia/Singapore), Windows, Python 3.11.15. The suite reported 23 passes and one skip for the POSIX-only FIFO case. All three examples matched their exact JSON stdout, empty stderr and exit codes.

| After file | Exit | Checked outcome |
| --- | --- | --- |
| `examples/retained-lock.json` | `0` | A relocated scoped package still has a reachable path |
| `examples/lost-lock.json` | `1` | The root and top-level worker lose two edges. The nested worker retains its edge. Missing-in-both and newly declared edges are omitted |
| `examples/linked-lock.json` | `2` | A package link makes the comparison unsupported |

The tests cover v2/v3 input, root and nested importers, scoped names, nearest ancestor selection, hoisted fallbacks, unrelated installed copies, exact spec matching, deterministic order, versionless placeholders, link and workspace rejection, bounds, UTF-8 and JSON errors, path redaction and a closed stdout pipe. The Windows path test verifies that explicit UNC and device paths, reserved names and alternate data streams are rejected before stat or open. The GitHub Actions workflow is configured to run both commands on Ubuntu and Windows with Python 3.11. The local run uses Windows and synthetic files.
