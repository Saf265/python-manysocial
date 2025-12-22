from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess, uuid, os, tempfile
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

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
