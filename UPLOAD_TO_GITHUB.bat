@echo off
setlocal
cd /d "%~dp0"
title FIFA 15 Local FUT - Upload to GitHub

set "GH=C:\Program Files\GitHub CLI\gh.exe"
if not exist "%GH%" set "GH=gh"

echo.
echo  FIFA 15 Local FUT - upload to GitHub
echo  ===================================
echo.
echo  This creates a PRIVATE repository on your GitHub account and pushes
echo  the project to it. Private is the default because this is
echo  reverse-engineering work on an EA title; you can make it public
echo  later from the repo's settings page.
echo.

"%GH%" --version >nul 2>&1
if errorlevel 1 goto nogh

echo  Step 1 of 3: signing in to GitHub
echo  --------------------------------
"%GH%" auth status >nul 2>&1
if errorlevel 1 (
    echo  A browser window will open. Approve the sign-in, then come back here.
    echo.
    "%GH%" auth login --hostname github.com --git-protocol https --web
    if errorlevel 1 goto authfailed
) else (
    echo  Already signed in.
)
echo.

echo  Step 2 of 3: creating the repository
echo  -----------------------------------
set "REPONAME=fifa15-local-fut"
"%GH%" repo view "%REPONAME%" >nul 2>&1
if errorlevel 1 (
    "%GH%" repo create "%REPONAME%" --private --source=. --remote=origin --description "Local Ultimate Team server for FIFA 15 PC, plus a fix for the Windows 11 boot crash"
    if errorlevel 1 goto createfailed
) else (
    echo  Repository already exists; reusing it.
    git remote remove origin >nul 2>&1
    for /f "delims=" %%u in ('"%GH%" repo view "%REPONAME%" --json url -q .url') do git remote add origin %%u.git
)
echo.

echo  Step 3 of 3: pushing
echo  -------------------
git push -u origin master
if errorlevel 1 (
    echo  master failed; trying main...
    git branch -M main
    git push -u origin main
    if errorlevel 1 goto pushfailed
)

echo.
echo  Done. Your repository:
"%GH%" repo view "%REPONAME%" --json url -q .url
echo.
echo  It is PRIVATE. To make it public later:
echo    Repo page - Settings - General - Danger Zone - Change visibility
goto end

:nogh
echo  GitHub CLI was not found. Install it with:
echo    winget install --id GitHub.cli
goto end

:authfailed
echo.
echo  Sign-in did not complete. Run this file again when ready.
goto end

:createfailed
echo.
echo  Could not create the repository. If the name is taken, rename it in
echo  this file (the REPONAME line) and run again.
goto end

:pushfailed
echo.
echo  The push failed. The message above says why.

:end
echo.
pause
