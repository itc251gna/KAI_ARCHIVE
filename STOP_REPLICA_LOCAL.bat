@echo off
chcp 65001 >nul
title KAI App Replica - Local Stop
echo ========================================================
echo KAI APP REPLICA - LOCAL STOP
echo ========================================================
docker compose -f docker-compose.local.yml down
echo.
echo Stopped.
pause
