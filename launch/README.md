# Job Right launch film

The 44.5-second film follows a saved Fernhill Freight role from a remote-only preference review to a tailored résumé and cover letter. The 1920 × 1080 and 1080 × 1920 edits use separate desktop and phone recordings of the actual application. Each includes English narration, an original instrumental score, open English captions and an end card linking to the product. The share kit adds English and Simplified Chinese SRT files and a 1280 × 720 thumbnail.

## Rebuild the share kit

From the repository root on macOS, with uv, Chromium and the system Baskerville, Georgia and Arial fonts available, run the commands below. The app and recording dependencies use the project's lockfile. The separate media environment keeps ONNX and the encoder out of the product install.

```sh
mkdir -p artifacts/film-build/models
uv venv --python 3.12 artifacts/film-build/venv
uv pip install --python artifacts/film-build/venv/bin/python kokoro-onnx==0.6.1 numpy==2.5.5 onnxruntime==1.32.0 soundfile==0.14.0 pillow==13.0.0 imageio-ffmpeg==0.8.2 qrcode==8.2
gh release download model-files-v1.0 --repo thewh1teagle/kokoro-onnx -p kokoro-v1.0.int8.onnx -p voices-v1.0.bin --dir artifacts/film-build/models
uv run --locked --group browser playwright install chromium
uv run --locked --group browser python launch/capture.py artifacts/film-build/wide
uv run --locked --group browser python launch/capture.py artifacts/film-build/tall --vertical
artifacts/film-build/venv/bin/python launch/narrate.py artifacts/film-build/models artifacts/film-build/audio
artifacts/film-build/venv/bin/python launch/render.py artifacts/film-build/wide artifacts/film-build/audio artifacts/launch
artifacts/film-build/venv/bin/python launch/render.py artifacts/film-build/tall artifacts/film-build/audio artifacts/launch --vertical
artifacts/film-build/venv/bin/python launch/package.py artifacts/launch
```

Each recording starts its own disposable synthetic workspace. Rendering and narration run locally once the dependencies and voice files have downloaded. See the [Kokoro model card](https://huggingface.co/hexgrad/Kokoro-82M) and the [ONNX model files](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0).

Use the widescreen cut for a standard YouTube video and the vertical cut for Shorts or a mobile X post. Both play with captions when muted. The ready-to-share files are in `artifacts/launch/`.
