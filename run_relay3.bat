@echo off
REM Change to project directory
cd /d C:\Users\marin\workspace\topstepx_api

REM Activate virtual environment
call trading_env\Scripts\activate.bat

REM Run Uvicorn server
uvicorn relay3:app --host 0.0.0.0 --port 5003 --http httptools