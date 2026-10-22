# Contributing to LocalConvert

Thanks for helping make LocalConvert better! Bug fixes, new formats, docs and design polish are all welcome.

## Ground rules

- Be kind. See the [Code of Conduct](CODE_OF_CONDUCT.md).
- One topic per pull request. Small PRs get reviewed fastest.
- **Files never leave the computer.** No analytics, telemetry, update checks or calls to online services, and no CDN scripts, fonts or images in the web UI (the Content-Security-Policy will block them anyway). A converter must work with Wi-Fi turned off.
- **Permissive licences only.** New dependencies must be MIT, BSD, Apache or similar, and must have prebuilt wheels for Windows and macOS. No AGPL/GPL libraries (PyMuPDF and pdf2docx were avoided on purpose).

## Getting set up

```sh
# Fork on GitHub, then:
git clone https://github.com/<you>/LocalConvert.git
cd LocalConvert
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python run.py                 # the app
.venv/bin/python run.py --widget        # just the click wheel
.venv/bin/python run.py --browser       # in your browser instead of a native window
```

On Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`.

Run the tests with `.venv/bin/python -m pytest -q`. They generate a sample of every input format and convert it to every target, so a new converter is tested automatically.

## Project layout

See [Project layout in the README](README.md#-project-layout). In short: converters live in `localconvert/converters/`, the conversion registry in `localconvert/core/registry.py`, the local server in `localconvert/server.py`, and the UI (plain HTML/CSS/JS, no build step) in `localconvert/web/`.

## Add a format in 3 steps

1. **Write the converter.** A function that takes `(src_path, out_dir, target, options)` and returns the list of output paths. Put it in the matching file in `localconvert/converters/`.
2. **Register it.** Decorate it with `@register("Name", sources, targets, engine=...)`. The UI, the click wheel, the CLI and `formats` pick it up automatically.

   ```python
   from ..core.registry import Options, register

   @register("Subtitles", ["srt", "vtt"], ["srt", "vtt"])
   def convert_subtitles(src: Path, out_dir: Path, target: str, opts: Options) -> List[Path]:
       ...
   ```
3. **Add a sample.** Add a small generated sample of the new input format to `tests/fixtures.py` so the test suite converts it to every target.

For documents, prefer writing a **reader** into `docmodel.Document`. One new reader instantly converts to PDF, Word, HTML, Markdown, text and images.

## UI changes

- The main window is `web/index.html` + `app.js` + `style.css`; the click wheel is `web/widget.*`; shared helpers are in `common.js`.
- Check your change in **light and dark** mode, and with *Reduce transparency* turned on.
- Add a screenshot or short screen recording to your PR.

## Pull request checklist

- `python -m pytest -q` passes.
- Tested on macOS or Windows (say which in the PR).
- Updated `CHANGELOG.md` under an *Unreleased* heading.
