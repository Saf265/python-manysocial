from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import subprocess, uuid, os, tempfile, logging, requests

# =========================
# CONFIG
# =========================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = FastAPI()

# =========================
# MODELS
# =========================
class Highlight(BaseModel):
    start_time: float
    end_time: float
    reason: str

class MergeRequest(BaseModel):
    url: str
    highlights: list[Highlight]

# =========================
# HELPERS
# =========================
def download_video(url: str, path: str):
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

def upload_to_vercel_blob(path: str) -> str:
    token = os.environ.get("VERCEL_BLOB_RW_TOKEN")
    if not token:
        raise Exception("Missing VERCEL_BLOB_RW_TOKEN")

    with open(path, "rb") as f:
        res = requests.post(
            "https://blob.vercel-storage.com/upload",
            headers={
                "Authorization": f"Bearer {token}"
            },
            files={
                "file": (os.path.basename(path), f, "video/mp4")
            }
        )

    res.raise_for_status()
    return res.json()["url"]

# =========================
# ROUTE /merge
# =========================
@app.post("/merge")
async def merge_video(request: MergeRequest):
    job_id = str(uuid.uuid4())
    tmp = tempfile.gettempdir()

    input_video = os.path.join(tmp, f"{job_id}_input.mp4")
    output_video = os.path.join(tmp, f"{job_id}_merged.mp4")
    concat_file = os.path.join(tmp, f"{job_id}_concat.txt")
    segments = []

    adapted_times = []
    current_time = 0.0

    try:
        print(f"\n--- MERGE JOB {job_id} ---")
        print("Downloading source video...")
        download_video(request.url, input_video)

        # 1️⃣ Découpe + recalcul des temps
        for i, h in enumerate(request.highlights):
            seg = os.path.join(tmp, f"{job_id}_seg_{i}.mp4")
            duration = h.end_time - h.start_time

            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(h.start_time),
                "-i", input_video,
                "-t", str(duration),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-c:a", "aac",
                seg
            ]

            subprocess.run(cmd, capture_output=True, text=True, check=True)
            segments.append(seg)

            adapted_times.append({
                "start_time": round(current_time, 2),
                "end_time": round(current_time + duration, 2),
                "reason": h.reason
            })

            print(
                f"[HIGHLIGHT] {round(current_time,2)}s → {round(current_time + duration,2)}s | {h.reason}"
            )

            current_time += duration

        # 2️⃣ Concat
        with open(concat_file, "w") as f:
            for s in segments:
                f.write(f"file '{s}'\n")

        subprocess.run([
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-c:a", "aac",
            output_video
        ], capture_output=True, text=True, check=True)

        # 3️⃣ Upload vers Vercel Blob
        print("Uploading merged video to Vercel Blob...")
        blob_url = upload_to_vercel_blob(output_video)

        return {
            "video_url": blob_url,
            "highlights": adapted_times,
            "duration": round(current_time, 2)
        }

    except subprocess.CalledProcessError as e:
        print("FFMPEG ERROR:", e.stderr)
        raise HTTPException(status_code=500, detail="FFmpeg failed")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        for f in [input_video, output_video, concat_file, *segments]:
            if f and os.path.exists(f):
                os.remove(f)
