# Third-party notices

## Perfetto

The official `perfetto==0.58.2` Python client is installed as a dependency from
PyPI. Its source and native Trace Processor binaries are not bundled in this
repository. For default local analysis, the client downloads and caches the
prebuilt selected by its own manifest, with SHA-256 verification on download.

- Project: https://github.com/google/perfetto
- Python package: https://pypi.org/project/perfetto/0.58.2/
- Copyright: The Android Open Source Project and upstream contributors.
- License: https://github.com/google/perfetto/blob/main/LICENSE

A copy of the upstream license and third-party notices remains in
`licenses/Perfetto-Apache-2.0.txt` in the source checkout and the installed
distribution's `.dist-info/licenses/` directory. Downloaded upstream artifacts
retain their own license and attribution.

This notice records third-party attribution and does not assign a license to
this project's original application code.
