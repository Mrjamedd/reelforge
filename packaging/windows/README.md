# ReelPush Studio Windows Packaging

Build the desktop executable and installer from a Windows machine:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\packaging\windows\build-windows.ps1
```

The build creates:

- `dist\ReelPush Studio\ReelPush Studio.exe`
- `dist\installer\ReelPushStudioSetup.exe` when Inno Setup 6 is installed

The installer uses the per-user app directory under `%LOCALAPPDATA%\Programs\ReelPush Studio`, adds a Start Menu shortcut, and offers an optional desktop shortcut.

Docker Desktop is still required at runtime because the desktop shell starts the local Postgres, Redis, and backend services through `docker compose`.
