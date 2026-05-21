@echo off
chcp 65001 >nul
title KAI App Replica - Local
echo ========================================================
echo KAI APP REPLICA - LOCAL START
echo ========================================================
echo URL: https://localhost:8443
echo PostgreSQL host port: 15432
echo.
docker compose -f docker-compose.local.yml up -d --build
echo.
echo Done. Open https://localhost:8443
pause
