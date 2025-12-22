from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
import subprocess, uuid, os, tempfile
import requests
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

def cleanup_files(*filenames):
    """Supprime les fichiers temporaires après la réponse."""
    for filename in filenames:
        try:
            if os.path.exists(filename):
                os.remove(filename)
                logger.info(f"Fichier supprimé : {filename}")
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de {filename} : {e}")

@app.post("/cut")
async def cut_video(url: str, start: str, end: str, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    tmp = tempfile.gettempdir()
    input_file = os.path.join(tmp, f"{job_id}_in.mp4")
    output_file = os.path.join(tmp, f"{job_id}_out.mp4")

    # Headers pour éviter d'être bloqué par certains CDN (comme Vercel Blob)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        # Télécharger la vidéo depuis l'URL (Vercel Blob)
        logger.info(f"Téléchargement de la vidéo : {url}")
        with requests.get(url, stream=True, headers=headers, timeout=30) as r:
            if r.status_code != 200:
                logger.error(f"Échec du téléchargement : {r.status_code}")
                raise HTTPException(status_code=400, detail="Impossible de télécharger la vidéo (URL invalide ou accès refusé)")
            
            with open(input_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        
        logger.info(f"Début de la découpe FFmpeg ({start} -> {end})")
        # Utilisation de FFmpeg pour couper la vidéo
        # On utilise -ss avant -i pour une découpe rapide, puis on peut ré-ajuster si besoin avec -to
        command = [
            "ffmpeg",
            "-ss", start,
            "-to", end,
            "-i", input_file,
            "-c", "copy",
            "-avoid_negative_ts", "1",
            output_file,
            "-y" # Overwrite
        ]
        
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        
        if result.returncode != 0:
            logger.error(f"Erreur FFmpeg : {result.stderr}")
            raise HTTPException(status_code=500, detail="Erreur lors du traitement de la vidéo")

        # Planifier la suppression des fichiers après l'envoi
        background_tasks.add_task(cleanup_files, input_file, output_file)

        # Retourner le fichier
        return FileResponse(
            path=output_file,
            filename=f"cut_{job_id}.mp4",
            media_type="video/mp4"
        )

    except requests.exceptions.RequestException as e:
        logger.error(f"Erreur de requête : {e}")
        cleanup_files(input_file)
        raise HTTPException(status_code=400, detail="Erreur lors de la récupération de la vidéo")
    except subprocess.TimeoutExpired:
        logger.error("Timeout FFmpeg")
        cleanup_files(input_file, output_file)
        raise HTTPException(status_code=408, detail="Le traitement a pris trop de temps")
    except Exception as e:
        logger.error(f"Erreur inattendue : {e}")
        cleanup_files(input_file, output_file)
        raise HTTPException(status_code=500, detail=str(e))
