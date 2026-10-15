"""紹介動画の録画：仮想の時計で 1 コマずつ撮って、MP4 と GIF にする

    uv run --with playwright python demo/ad/record.py [--preview 15,30,60]

--preview：指定した秒の静止画だけを撮る（確認用）。
Chrome（インストール済みのもの）を使う。日本語フォント：ヒラギノ（macOS）、コード：HackGen。
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FPS = 30


def main() -> None:
    preview = None
    if "--preview" in sys.argv:
        preview = [float(x) for x in sys.argv[sys.argv.index("--preview") + 1].split(",")]
    frames = Path(tempfile.mkdtemp(prefix="dandori-ad-"))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport={"width": 1280, "height": 720}, device_scale_factor=1)
        page.goto((HERE / "index.html").as_uri() + "?record")
        page.wait_for_timeout(500)                       # フォントの読み込み
        duration = page.evaluate("window.DURATION")
        times = preview or [i / FPS for i in range(int(duration * FPS))]
        for i, t in enumerate(times):
            page.evaluate(f"seek({t})")
            name = f"t{t:05.1f}.png" if preview else f"{i:05d}.png"
            page.screenshot(path=str(frames / name))
        browser.close()
    if preview:
        print(frames)
        return
    mp4, gif = HERE.parent / "dandori.mp4", HERE.parent / "dandori.gif"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(FPS), "-i", str(frames / "%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)], check=True)
    # README 用の GIF：GitHub で表示できる大きさ（10MB 未満）に。幅 854、10 コマ／秒、色を最適化
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(mp4), "-vf",
                    "fps=10,scale=854:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];"
                    "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle", str(gif)], check=True)
    shutil.rmtree(frames)
    print(mp4, gif)


if __name__ == "__main__":
    main()
