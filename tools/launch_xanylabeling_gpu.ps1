param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvRoot = Join-Path $repoRoot ".venv-cu12"
$pythonExe = Join-Path $venvRoot "Scripts\python.exe"
$sitePackages = Join-Path $venvRoot "Lib\site-packages"
$nvidiaRoot = Join-Path $sitePackages "nvidia"

if (-not (Test-Path $pythonExe)) {
    throw "GPU launcher target not found: $pythonExe"
}

$dllDirs = @(
    "cuda_runtime\bin",
    "cuda_nvrtc\bin",
    "cublas\bin",
    "cudnn\bin",
    "cufft\bin",
    "curand\bin",
    "cusparse\bin",
    "cusolver\bin",
    "nvjitlink\bin",
    "nvtx\bin"
) | ForEach-Object { Join-Path $nvidiaRoot $_ } | Where-Object { Test-Path $_ }

if ($dllDirs.Count -eq 0) {
    Write-Warning "No NVIDIA runtime DLL directories were found. Launching X-AnyLabeling directly."
} else {
    $env:PATH = (($dllDirs + $env:PATH) -join ";")
}

$env:PYTHONPATH = (($repoRoot + ";" + $env:PYTHONPATH).TrimEnd(";"))

Write-Host "X-AnyLabeling GPU launcher"
Write-Host "Repo: $repoRoot"
Write-Host "Python: $pythonExe"
Write-Host "App: python -m anylabeling.app"

if ($dllDirs.Count -gt 0) {
    Write-Host "CUDA DLL dirs:"
    $dllDirs | ForEach-Object { Write-Host "  - $_" }
}

& $pythonExe -m anylabeling.app @Args
