#!/bin/bash
cd /Users/temp/aitrading
source venv/bin/activate
exec uvicorn server:app --host 0.0.0.0 --port 8000
