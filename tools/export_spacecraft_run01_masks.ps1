param(
    [ValidateSet("A", "B")]
    [string]$Domain = "A",
    [string]$RepoRoot = "",
    [string]$StagingRoot = "",
    [string]$ImagesA = "",
    [string]$ImagesB = "",
    [string]$ForegroundLabel = "spacecraft"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if ([string]::IsNullOrWhiteSpace($StagingRoot)) {
    $StagingRoot = (Resolve-Path (Join-Path $RepoRoot "..\..\spacecraft_render2real_staging\run_01")).Path
}
if ([string]::IsNullOrWhiteSpace($ImagesA)) {
    $ImagesA = (Resolve-Path (Join-Path $RepoRoot "..\..\src")).Path
}
if ([string]::IsNullOrWhiteSpace($ImagesB)) {
    $ImagesB = (Resolve-Path (Join-Path $RepoRoot "..\..\100CANON")).Path
}

$python = Join-Path $RepoRoot ".venv-cu12\Scripts\python.exe"
$exportScript = Join-Path $RepoRoot "tools\export_masks_from_xanylabeling.py"
$reviewDir = Join-Path $StagingRoot "review"

if ($Domain -eq "A") {
    $labelsDir = Join-Path $StagingRoot "labels_A"
    $outputDir = Join-Path $StagingRoot "masks_A_raw"
    $imagesDir = $ImagesA
    $missingReport = Join-Path $reviewDir "missing_mask_export_A.txt"
    $emptyReport = Join-Path $reviewDir "empty_annotations_A.txt"
} else {
    $labelsDir = Join-Path $StagingRoot "labels_B"
    $outputDir = Join-Path $StagingRoot "masks_B_raw"
    $imagesDir = $ImagesB
    $missingReport = Join-Path $reviewDir "missing_mask_export_B.txt"
    $emptyReport = Join-Path $reviewDir "empty_annotations_B.txt"
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python not found: $python"
}
if (-not (Test-Path -LiteralPath $exportScript)) {
    throw "Export script not found: $exportScript"
}
if (-not (Test-Path -LiteralPath $labelsDir)) {
    throw "Labels directory not found: $labelsDir"
}
if (-not (Test-Path -LiteralPath $imagesDir)) {
    throw "Images directory not found: $imagesDir"
}

New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
New-Item -ItemType Directory -Path $reviewDir -Force | Out-Null

Write-Host "Exporting domain $Domain masks..."
Write-Host "Labels:  $labelsDir"
Write-Host "Images:  $imagesDir"
Write-Host "Output:  $outputDir"
Write-Host "Missing: $missingReport"
Write-Host "Empty:   $emptyReport"

& $python $exportScript `
    --labels $labelsDir `
    --output $outputDir `
    --images $imagesDir `
    --foreground-label $ForegroundLabel `
    --missing-report $missingReport `
    --empty-report $emptyReport

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
