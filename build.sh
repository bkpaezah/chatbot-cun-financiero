#!/usr/bin/env bash
set -e

# Instalar el driver ODBC de SQL Server (Microsoft) en el entorno Linux de Render
curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
curl https://packages.microsoft.com/config/debian/12/prod.list > /etc/apt/sources.list.d/mssql-release.list
apt-get update
ACCEPT_EULA=Y apt-get install -y msodbcsql17 unixodbc-dev

# Instalar las dependencias de Python
pip install -r requirements.txt
