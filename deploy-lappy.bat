@echo off
REM Alphabetty — Lappy (192.168.0.33) Deploy Script
REM Run from Rog: python -c "import sys; sys.path.insert(0,'C:/Users/aaron/Desktop'); import lappy_ssh as ssh; print(ssh.run_lappy(r'deploy-lappy.bat', timeout=600)[0])"
REM Or SSH into Lappy and run: deploy-lappy.bat
REM
REM Prereqs: Windows 11, Docker Desktop, Git for Windows, SSH enabled

setlocal enabledelayedexpansion
set "BASE=C:\Users\aaron\Desktop\alphabetty"

echo === Alphabetty - Lappy Deploy ===
echo Base: %BASE%
echo.

REM ─── 1. Git init + sync ───
echo [1/3] Syncing from GitHub...
if not exist "%BASE%\.git" (
    git -C "%BASE%" init
    git -C "%BASE%" remote add origin https://github.com/slothitude/alphabetty.git
    echo Git repo initialized
) else (
    echo Git repo exists
)

git -C "%BASE%" fetch origin
git -C "%BASE%" reset origin/master
git -C "%BASE%" checkout -- .
for /f "tokens=*" %%i in ('git -C "%BASE%" log --oneline -1') do echo Synced to %%i

REM ─── 2. Write docker-compose.override.yml ───
echo.
echo [2/3] Writing docker-compose.override.yml...
if not exist "%BASE%\docker-compose.override.yml" (
    echo ERROR: docker-compose.override.yml must already exist with API keys.
    echo Create it manually or copy from a previous deployment.
    echo See deploy-lappy.example.override.yml for the template.
    exit /b 1
)
echo Override written

REM ─── 3. Build and deploy ───
echo.
echo [3/3] Building and deploying...
docker compose --project-directory "%BASE%" up --build -d

echo.
echo === Deploy Complete ===
echo.
echo Lappy capabilities: browser, gpu, llm_heavy, search, image_gen
echo Swarm peer: oracle (152.69.184.137:7700)
echo.
echo Verify:  curl -s http://localhost:7700/api/v1/swarm/status
echo Redeploy: deploy-lappy.bat
