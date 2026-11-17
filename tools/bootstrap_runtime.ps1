<#
.SYNOPSIS
    Descarga y configura el runtime portable Python 3.12.7 Embeddable para WAckup.
.DESCRIPTION
    Descarga Python 3.12.7 Embeddable (amd64) desde python.org con verificación
    criptográfica SHA-256, habilita site-packages, instala pip vía get-pip.py
    e instala las dependencias de requirements.txt dentro de runtime\python\.
#>
param(
    [string]$RootDir = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$RootDir = $RootDir.TrimEnd('\', '/')
$RuntimeDir = Join-Path $RootDir "runtime\python"
$ReqFile = Join-Path $RootDir "requirements.txt"

$PythonEmbedUrl = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
$PythonEmbedSha256 = "0d57bb6cb078b74d23dbfe91f77d6780d45bed328911609f1f7ee2ba1606bf44"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

if (-not (Test-Path $ReqFile)) {
    Write-Host "[ERROR] No se encontro requirements.txt en: $ReqFile" -ForegroundColor Red
    exit 1
}

$TempDir = Join-Path $env:TEMP ("wackup_bootstrap_" + [System.Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $TempDir -Force | Out-Null

try {
    $PythonZip = Join-Path $TempDir "python-3.12.7-embed-amd64.zip"
    $GetPipScript = Join-Path $TempDir "get-pip.py"

    Write-Host ""
    Write-Host "[1/4] Descargando Python 3.12.7 Embeddable oficial (python.org)..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $PythonEmbedUrl -OutFile $PythonZip -UseBasicParsing -TimeoutSec 120

    Write-Host "[2/4] Verificando integridad criptografica SHA-256..." -ForegroundColor Cyan
    $ActualHash = (Get-FileHash -Path $PythonZip -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne $PythonEmbedSha256.ToLowerInvariant()) {
        throw "Fallo de integridad SHA-256. Esperado: $PythonEmbedSha256 | Obtenido: $ActualHash"
    }
    Write-Host "      SHA-256 verificado correctamente." -ForegroundColor Green

    if (Test-Path $RuntimeDir) {
        Remove-Item -Path $RuntimeDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

    Write-Host "[3/4] Extrayendo y configurando runtime\python..." -ForegroundColor Cyan
    Expand-Archive -Path $PythonZip -DestinationPath $RuntimeDir -Force

    # Habilitar import site y Lib/site-packages en python312._pth
    $PthFiles = Get-ChildItem -Path $RuntimeDir -Filter "*._pth"
    if ($PthFiles.Count -gt 0) {
        $PthPath = $PthFiles[0].FullName
        $Lines = Get-Content -Path $PthPath -Encoding UTF8
        $NewLines = New-Object System.Collections.Generic.List[string]
        $HasSitePackages = $false

        foreach ($Line in $Lines) {
            $Trimmed = $Line.Trim()
            if ($Trimmed -eq "#import site" -or $Trimmed -eq "import site") {
                $NewLines.Add("import site")
            }
            elseif ($Trimmed -like "*site-packages*") {
                $HasSitePackages = $true
                $NewLines.Add($Line)
            }
            else {
                $NewLines.Add($Line)
            }
        }

        if (-not $HasSitePackages) {
            $NewLines.Insert(0, "Lib/site-packages")
            $NewLines.Insert(1, ".")
        }

        $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::WriteAllLines($PthPath, $NewLines, $Utf8NoBom)
    }

    New-Item -ItemType Directory -Path (Join-Path $RuntimeDir "Lib\site-packages") -Force | Out-Null

    Write-Host "[4/4] Instalando pip y dependencias de WAckup (requirements.txt)..." -ForegroundColor Cyan
    $PythonExe = Join-Path $RuntimeDir "python.exe"

    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipScript -UseBasicParsing -TimeoutSec 120
    & $PythonExe $GetPipScript --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        throw "Error instalando pip en runtime\python (codigo $LASTEXITCODE)."
    }

    & $PythonExe -m pip install --no-warn-script-location -r $ReqFile
    if ($LASTEXITCODE -ne 0) {
        throw "Error instalando dependencias desde requirements.txt (codigo $LASTEXITCODE)."
    }

    # Guardar hash de requirements.txt para detectar futuras actualizaciones
    $ReqHash = (Get-FileHash -Path $ReqFile -Algorithm SHA256).Hash.ToLowerInvariant()
    $ReqHashFile = Join-Path $RuntimeDir ".req_sha256"
    [System.IO.File]::WriteAllText($ReqHashFile, $ReqHash)

    Write-Host ""
    Write-Host "[OK] Entorno portable configurado con exito en runtime\python." -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ""
    Write-Host "[ERROR] No se pudo configurar el entorno portable: $($_.Exception.Message)" -ForegroundColor Red
    if (Test-Path $RuntimeDir) {
        Remove-Item -Path $RuntimeDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 1
}
finally {
    if (Test-Path $TempDir) {
        Remove-Item -Path $TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
