#!/bin/bash
cd /workspaces/faceless-video-platform
sudo service postgresql start
sudo service redis-server start
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
