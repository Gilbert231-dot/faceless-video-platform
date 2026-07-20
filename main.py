import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from tasks import generate_video
from celery.result import AsyncResult

app = FastAPI()

# Mount the output folder for video downloads
app.mount("/output", StaticFiles(directory="/workspaces/faceless-video-platform"), name="output")

@app.get("/")
def read_root():
    return {"message": "Welcome to your Faceless Video Platform!"}

@app.get("/generate")
def start_generation(topic: str):
    task = generate_video.delay(topic)
    return {"task_id": task.id, "status": "processing"}

@app.get("/result/{task_id}")
def get_result(task_id: str):
    task = AsyncResult(task_id, app=generate_video)
    if task.ready():
        return task.result
    return {"status": "pending"}

@app.get("/health/db")
def check_db():
    return {"database": "connected"}

@app.get("/health/redis")
def check_redis():
    return {"redis": "connected"}