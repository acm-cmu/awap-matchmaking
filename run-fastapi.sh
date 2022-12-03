#!/bin/bash

if [ $# -eq 0 ]; then
    echo "Usage: $0 <HOST> [PORT]"
    exit 1
fi

PORT=${2:-8000}
FASTAPI_HOSTNAME=$1 uvicorn main:app --host 0.0.0.0
