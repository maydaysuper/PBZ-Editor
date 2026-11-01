param([string]$CargoPath = 'cargo', [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$nativeRoot = Join-Path $projectRoot 'native'
$outputRoot = Join-Path $projectRoot '.local\native'
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$oldFlags = $env:RUSTFLAGS
$oldPath = $env:PATH
try {
    $rustc = if (Split-Path -Parent $CargoPath) { Join-Path (Split-Path -Parent $CargoPath) 'rustc.exe' } else { 'rustc' }
    $sysroot = (& $rustc --print sysroot).Trim()
    $hostTriple = ((& $rustc -vV | Select-String '^host:').Line -replace '^host:\s*', '').Trim()
    $env:PATH = (Join-Path $sysroot "lib\rustlib\$hostTriple\bin\self-contained") + ';' + $oldPath
    $env:RUSTFLAGS = '-C linker=rust-lld -C target-feature=+crt-static'
    if ($hostTriple -ne 'x86_64-pc-windows-gnu') { throw 'Use the x86_64-pc-windows-gnu Rust toolchain.' }
    & $CargoPath build --release --locked --manifest-path (Join-Path $nativeRoot 'Cargo.toml')
    if ($LASTEXITCODE -ne 0) { throw 'Open decoder build failed.' }
    Copy-Item -LiteralPath (Join-Path $nativeRoot 'target\release\pbz_codec.dll') -Destination $outputRoot -Force
    $hash = (Get-FileHash -LiteralPath (Join-Path $outputRoot 'pbz_codec.dll') -Algorithm SHA256).Hash.ToLowerInvariant()
    @{ abi = 1; library = 'pbz_codec.dll'; sha256 = $hash; decoder = 'oozextract'; version = '0.5.7'; license = 'MIT' } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outputRoot 'manifest.json') -Encoding utf8
    & $CargoPath tree --locked --edges normal --prefix none --format '{p}' --manifest-path (Join-Path $nativeRoot 'Cargo.toml') |
        Set-Content -LiteralPath (Join-Path $outputRoot 'packages.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Cannot enumerate native dependencies.' }
    $cargoHome = if ($env:CARGO_HOME) { $env:CARGO_HOME } else { Join-Path $env:USERPROFILE '.cargo' }
    & $PythonPath (Join-Path $projectRoot 'tools\release_contents.py') native-notices $cargoHome --rust-sysroot $sysroot
    if ($LASTEXITCODE -ne 0) { throw 'Cannot collect native notices.' }
    Write-Output "Ready: $outputRoot\pbz_codec.dll"
} finally { $env:RUSTFLAGS = $oldFlags; $env:PATH = $oldPath }
