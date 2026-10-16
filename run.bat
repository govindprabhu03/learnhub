@echo off
cd /d "%~dp0backend"
"C:\Users\prabh\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn main:app --port 8100
