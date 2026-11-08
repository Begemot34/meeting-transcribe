# Contributing

```bash
make check    # ruff, shellcheck, bash -n and the tests; make lint / make test run one half
```

Needs [uv](https://docs.astral.sh/uv/) and [ShellCheck](https://www.shellcheck.net). The checks run in their own
environment, `.venv-check`, with only the `test` dependency group: no torch or models, and the `.venv` that
`./transcribe setup` made stays as it is. CI runs the same `make lint` and `make test` on Linux and macOS.

- Every script in `scripts/` is a standalone program that `./transcribe` runs as `python -I scripts/x.py`.
  Logic that is worth testing lives in functions; the work happens in `main()` or at the end of the file.
- Tests use a synthetic meeting in `tests/data/meeting`: recognition, diarization and OCR are not run, they need
  gigabytes of models — check them by hand on a real recording.
- The service (`scripts/serve*.py`) is tested against `tests/data/fake_transcribe`, a stub that prints the
  pipeline's log lines and writes its files in no time: the queue, the inbox, the API and the progress parsing run
  for real, the pipeline does not. If a step's output lines change, update the stub and `STEPS` in
  `scripts/serve_worker.py` together.
- A new dependency goes into `pyproject.toml`, then `uv lock`; the image and CI install from `uv.lock`.
- Run the tests through `make test`, not a bare `uv run`: that one would reduce the app's `.venv` to the test group.
- `tests/golden/` is the expected `merge` output for that meeting. If you change the output on purpose, update the
  files and review the diff.
- A new option or `MT_*` variable goes to [COMMANDS.md](COMMANDS.md) or [SETTINGS.md](SETTINGS.md) and their
  Russian versions: `tests/test_docs.py` fails otherwise.
