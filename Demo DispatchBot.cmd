@echo off
rem Abre a janela de demonstração (preenche GMUD ou Backup no ServiceNow, sem enviar)
cd /d "%~dp0"
start "" pythonw robo\demo.py
