from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess, uuid, os, tempfile
import logging
import requests
from typing import List, Optional
from vercel.blob import AsyncBlobClient

# 🔐 Token Vercel Blob en dur (ou via env)
# VERCEL_BLOB_TOKEN = os.getenv("VERCEL_BLOB_TOKEN", "VERCEL_BLOB_RW_TOKEN_ICI")
# VERCEL_BLOB_TOKEN = "vercel_blob_rw_accHkx67jxPygQwf_UJyLUNalcvYHm0lPniVTNkiBpMiEV8"
app = FastAPI()
# blob_client = AsyncBlobClient()
blob_client = AsyncBlobClient(token="vercel_blob_rw_accHkx67jxPygQwf_UJyLUNalcvYHm0lPniVTNkiBpMiEV8")



class Highlight(BaseModel):
    start_time: float
    end_time: float
    reason: Optional[str] = None


class MergeRequest(BaseModel):
    video_url: str
    highlights: List[Highlight]


# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Modèle pour le corps de la requête JSON
class CutRequest(BaseModel):
    url: str
    start: str
    end: str

def cleanup_files(*filenames):
    """Supprime les fichiers temporaires après la réponse."""
    print(f"\n[CLEANUP] Début du nettoyage des fichiers...")
    for filename in filenames:
        try:
            if os.path.exists(filename):
                os.remove(filename)
                print(f"[CLEANUP] Fichier supprimé : {filename}")
                logger.info(f"Fichier supprimé : {filename}")
        except Exception as e:
            print(f"[CLEANUP] Erreur lors de la suppression de {filename} : {e}")
            logger.error(f"Erreur lors de la suppression de {filename} : {e}")

@app.post("/cut")
async def cut_video(request: CutRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    tmp = tempfile.gettempdir()
    output_file = os.path.join(tmp, f"{job_id}_out.mp4")

    print(f"\n--- NOUVELLE REQUÊTE JSON [{job_id}] ---")
    print(f"URL: {request.url}")
    print(f"Periode: {request.start} -> {request.end}")

    try:
        print(f"[1/2] Découpe FFmpeg directe depuis l'URL...")
        logger.info(f"Découpe FFmpeg ({request.start} -> {request.end}) pour {request.url}")

        # Commande FFmpeg : lecture directe depuis l'URL
        command = [
            "ffmpeg",
            "-ss", request.start,
            "-to", request.end,
            "-i", request.url,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            output_file,
            "-y"
        ]

        print(f"Running command: {' '.join(command)}")
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)

        if result.returncode != 0:
            print(f"ERROR: Erreur FFmpeg : {result.stderr}")
            logger.error(f"Erreur FFmpeg : {result.stderr}")
            raise HTTPException(status_code=500, detail="Erreur lors du traitement de la vidéo")

        print(f"FFmpeg terminé avec succès.")

        # Planifier la suppression du fichier après l'envoi
        print(f"[2/2] Envoi du fichier et planification du nettoyage...")
        background_tasks.add_task(cleanup_files, output_file)

        return FileResponse(
            path=output_file,
            filename=f"cut_{job_id}.mp4",
            media_type="video/mp4"
        )

    except subprocess.TimeoutExpired:
        print("ERROR: Timeout FFmpeg")
        logger.error("Timeout FFmpeg")
        cleanup_files(output_file)
        raise HTTPException(status_code=408, detail="Le traitement a pris trop de temps")
    except Exception as e:
        print(f"ERROR INATTENDUE: {e}")
        logger.error(f"Erreur inattendue : {e}")
        cleanup_files(output_file)
        raise HTTPException(status_code=500, detail=str(e))




@app.post("/merge")
async def merge(payload: MergeRequest):
    if not payload.highlights:
        raise HTTPException(status_code=400, detail="No highlights provided")

    with tempfile.TemporaryDirectory() as tmp:
        source_path = f"{tmp}/source.mp4"

        # 1️⃣ Download video
        try:
            with open(source_path, "wb") as f:
                r = requests.get(payload.video_url, stream=True, timeout=30)
                r.raise_for_status()
                for chunk in r.iter_content(1024 * 1024):
                    f.write(chunk)
        except Exception:
            raise HTTPException(status_code=400, detail="Failed to download video")

        clip_paths = []
        new_timestamps = []
        current_pos = 0.0

        # 2️⃣ Cut highlights & compute new timestamps
        for i, h in enumerate(payload.highlights):
            if h.end_time <= h.start_time:
                continue

            duration = h.end_time - h.start_time
            clip_path = f"{tmp}/clip_{i}.mp4"

            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", str(h.start_time),
                    "-i", source_path,
                    "-t", str(duration),
                    "-c", "copy",
                    clip_path
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

            clip_paths.append(clip_path)

            # compute new timestamps
            new_timestamps.append({
                # "original_start": h.start_time,
                # "original_end": h.end_time,
                "reason": h.reason,
                "new_start": round(current_pos, 3),
                "new_end": round(current_pos + duration, 3)
            })
            current_pos += duration

        if not clip_paths:
            raise HTTPException(status_code=400, detail="No valid clips generated")

        # 3️⃣ Concat file
        list_file = f"{tmp}/list.txt"
        with open(list_file, "w") as f:
            for p in clip_paths:
                f.write(f"file '{p}'\n")

        output_path = f"{tmp}/highlights.mp4"

        # 4️⃣ Merge clips
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                output_path
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 5️⃣ Upload vers Vercel Blob (token ICI)
        with open(output_path, "rb") as f:
            blob = await blob_client.put(
                "videos/highlights.mp4",
                f.read(),
                access="public",
                content_type="video/mp4",
                add_random_suffix=True,
                # token=VERCEL_BLOB_TOKEN
            )

        return {
            # "highlights_count": len(clip_paths),
            "video_url": blob.url,
            "new_timestamps": new_timestamps
        }
