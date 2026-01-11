param(
  [Parameter(Mandatory=$false)]
  [string]$ProjectRoot = (Resolve-Path "${PSScriptRoot}\.." ).Path
)

$pth = Join-Path $ProjectRoot ".venv\Lib\site-packages\_notifyhub_security_digest.pth"
$src = Join-Path $ProjectRoot "src"

if(-not (Test-Path $pth)){
  Write-Host "No editable .pth found: $pth"
  exit 0
}

$enc = [System.Text.Encoding]::GetEncoding(932)
[System.IO.File]::WriteAllText($pth, $src + "`n", $enc)
Write-Host "Rewrote $pth as cp932."
