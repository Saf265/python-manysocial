from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import subprocess, uuid, os, tempfile
import requests

app = FastAPI()

@app.post("/cut")
def cut_video(url: str, start: str, end: str):
    job_id = str(uuid.uuid4())
    tmp = tempfile.gettempdir()
    input_file = os.path.join(tmp, f"{job_id}_in.mp4")
    output_file = os.path.join(tmp, f"{job_id}_out.mp4")

    try:
        # Télécharger la vidéo
        r = requests.get(url, stream=True)
        if r.status_code != 200:
            raise HTTPException(400, "Impossible de télécharger la vidéo")
        with open(input_file, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)

        # Coupe par temps
        subprocess.run([
            "ffmpeg",
            "-ss", start,
            "-to", end,
            "-i", input_file,
            "-c", "copy",
            output_file
        ], check=True, timeout=600)

        # Stream du résultat
        return StreamingResponse(open(output_file, "rb"), media_type="video/mp4")

    except subprocess.TimeoutExpired:
        raise HTTPException(408, "Timeout FFmpeg")
    finally:
        # Nettoyage du fichier d'entrée uniquement
        if os.path.exists(input_file):
            os.remove(input_file)