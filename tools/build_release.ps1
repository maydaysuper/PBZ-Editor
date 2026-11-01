param([string]$PythonPath, [switch]$Zip, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$version = '0.2.0'
$distRoot = Join-Path $projectRoot "dist\v$version"
if ($OutputDirectory) {
    $distRoot = if ([IO.Path]::IsPathRooted($OutputDirectory)) { [IO.Path]::GetFullPath($OutputDirectory) } else { [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputDirectory)) }
}
$buildRoot = Join-Path $projectRoot 'build'
$releaseRoot = Join-Path $distRoot 'PBZ-Editor'
foreach ($file in @('launcher.py', 'data\profile.json', '.local\native\pbz_codec.dll', '.local\native\manifest.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $file) -PathType Leaf)) { throw "Required file is missing: $file" }
}
if (Test-Path -LiteralPath (Join-Path $releaseRoot '.local')) { throw 'Existing portable user data found. Back it up and use a fresh output directory.' }
if (Get-Process PBZ-Editor -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $releaseRoot 'PBZ-Editor.exe') }) { throw 'Close this release before rebuilding.' }
$installerArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
    '--name', 'PBZ-Editor', '--distpath', $distRoot, '--workpath', $buildRoot,
    '--specpath', $buildRoot, '--paths', $projectRoot,
    '--additional-hooks-dir', (Join-Path $projectRoot 'tools\hooks'),
    '--add-data', ((Join-Path $projectRoot 'data\profile.json') + ';data'),
    '--add-data', ((Join-Path $projectRoot 'pbz_editor\assets') + ';pbz_editor/assets'),
    '--add-data', ((Join-Path $projectRoot 'pbz_editor\locales') + ';pbz_editor/locales'),
    '--add-data', ((Join-Path $projectRoot '.local\native\manifest.json') + ';native'),
    '--add-binary', ((Join-Path $projectRoot '.local\native\pbz_codec.dll') + ';native'),
    '--exclude-module', 'PySide6.QtWebEngineCore', '--exclude-module', 'PySide6.QtWebEngineWidgets',
    '--exclude-module', 'PySide6.QtWebEngineQuick', '--exclude-module', 'PySide6.QtPdf',
    '--exclude-module', 'PySide6.QtPdfWidgets', '--exclude-module', 'PySide6.QtQml',
    '--exclude-module', 'PySide6.QtQuick', '--exclude-module', 'PySide6.QtQuickWidgets',
    '--exclude-module', 'pytest', '--exclude-module', 'tkinter', (Join-Path $projectRoot 'launcher.py')
)
Push-Location -LiteralPath $projectRoot
$originalPath = $env:PATH
$pythonBase = (& $PythonPath -c 'import sys; print(sys.base_prefix)').Trim()
$env:PATH = @((Split-Path -Parent $PythonPath), $pythonBase, (Join-Path $env:SystemRoot 'System32'), $env:SystemRoot) -join ';'
try {
    & $PythonPath @installerArgs
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    foreach ($file in @('README.md', 'README.en.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $releaseRoot
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\quick-start.txt') -Destination (Join-Path $releaseRoot 'START-HERE.txt')
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docs\quick-start.en.txt') -Destination (Join-Path $releaseRoot 'START-HERE.en.txt')
    & $PythonPath tools\release_contents.py finalize $releaseRoot
    if ($LASTEXITCODE -ne 0) { throw 'Release content verification failed.' }
    if ($Zip) {
        $binaryZip = Join-Path $distRoot "PBZ-Editor-$version-windows-x64.zip"
        Compress-Archive -LiteralPath $releaseRoot -DestinationPath $binaryZip -Force
        $sourceZip = Join-Path $distRoot "PBZ-Editor-$version-source.zip"
        & $PythonPath tools\release_contents.py source $sourceZip
        if ($LASTEXITCODE -ne 0) { throw 'Source archive verification failed.' }
        @($binaryZip, $sourceZip) | ForEach-Object {
            $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $([IO.Path]::GetFileName($_))"
        } | Set-Content -LiteralPath (Join-Path $distRoot 'SHA256SUMS.txt') -Encoding ascii
    }
    Write-Host "Ready: $releaseRoot\PBZ-Editor.exe"
} finally { $env:PATH = $originalPath; Pop-Location }
