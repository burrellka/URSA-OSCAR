param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$DockerHubUser = "brain40",
  [switch]$SkipPush
)

Write-Host "Starting Build and Push Process for URSA-OSCAR (Version: $Version)" -ForegroundColor Cyan

$Services = @(
    @{ Name="ursa-oscar-api"; Context="backend" },
    @{ Name="ursa-oscar-mcp"; Context="mcp-server" },
    @{ Name="ursa-oscar-web"; Context="frontend" },
    @{ Name="ursa-oscar-watcher"; Context="watcher" }
)

foreach ($svc in $Services) {
    $ImageName = "$($DockerHubUser)/$($svc.Name)"

    Write-Host "Building $($svc.Name)..." -ForegroundColor Yellow
    docker build -t "$ImageName`:$Version" -t "$ImageName`:latest" -f ".\$($svc.Context)\Dockerfile" .

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Build failed for $($svc.Name)!" -ForegroundColor Red
        exit 1
    }

    if ($SkipPush) {
        Write-Host "Skipping push for $($svc.Name) (--SkipPush)" -ForegroundColor DarkGray
        continue
    }

    Write-Host "Pushing $($svc.Name)..." -ForegroundColor Cyan
    docker push "$ImageName`:$Version"
    docker push "$ImageName`:latest"

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Push failed for $($svc.Name)!" -ForegroundColor Red
        exit 1
    }
}

Write-Host "Build and Push Complete!" -ForegroundColor Green
