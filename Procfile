web: uvicorn main:app --host 0.0.0.0 --port $PORT
worker: sh -c 'while true; do python worker.py; sleep 60; done'
