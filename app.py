from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess, uuid, os, tempfile
import requests
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
    input_file = os.path.join(tmp, f"{job_id}_in.mp4")
    output_file = os.path.join(tmp, f"{job_id}_out.mp4")

    print(f"\n--- NOUVELLE REQUÊTE JSON [{job_id}] ---")
    print(f"URL: {request.url}")
    print(f"Periode: {request.start} -> {request.end}")

    # Headers pour éviter d'être bloqué par certains CDN (comme Vercel Blob)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        # Télécharger la vidéo depuis l'URL (Vercel Blob)
        print(f"[1/3] Téléchargement de la vidéo en cours...")
        logger.info(f"Téléchargement de la vidéo : {request.url}")
        
        with requests.get(request.url, stream=True, headers=headers, timeout=30) as r:
            if r.status_code != 200:
                print(f"ERROR: Échec du téléchargement (Status: {r.status_code})")
                logger.error(f"Échec du téléchargement : {r.status_code}")
                raise HTTPException(status_code=400, detail="Impossible de télécharger la vidéo (URL invalide ou accès refusé)")
            
            with open(input_file, "wb") as f:
                downloaded = 0
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                print(f"Done. {downloaded} octets téléchargés.")
        
        print(f"[2/3] Début de la découpe FFmpeg...")
        logger.info(f"Début de la découpe FFmpeg ({request.start} -> {request.end})")
        
        # Configuration de la commande FFmpeg
        command = [
            "ffmpeg",
            "-ss", request.start,
            "-to", request.end,
            "-i", input_file,
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

        # Planifier la suppression des fichiers après l'envoi
        print(f"[3/3] Envoi du fichier et planification du nettoyage...")
        background_tasks.add_task(cleanup_files, input_file, output_file)

        return FileResponse(
            path=output_file,
            filename=f"cut_{job_id}.mp4",
            media_type="video/mp4"
        )

    except requests.exceptions.RequestException as e:
        print(f"ERROR REQUETE: {e}")
        logger.error(f"Erreur de requête : {e}")
        cleanup_files(input_file)
        raise HTTPException(status_code=400, detail="Erreur lors de la récupération de la vidéo")
    except subprocess.TimeoutExpired:
        print("ERROR: Timeout FFmpeg")
        logger.error("Timeout FFmpeg")
        cleanup_files(input_file, output_file)
        raise HTTPException(status_code=408, detail="Le traitement a pris trop de temps")
    except Exception as e:
        print(f"ERROR INATTENDUE: {e}")
        logger.error(f"Erreur inattendue : {e}")
        cleanup_files(input_file, output_file)
        raise HTTPException(status_code=500, detail=str(e))


