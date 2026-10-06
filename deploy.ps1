param([string]$ProjectId)

$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath '.env')) {
    throw 'Missing local .env file.'
}

$settings = @{}
Get-Content -LiteralPath '.env' | ForEach-Object {
    if ($_ -match '^([A-Za-z_][A-Za-z_0-9]*)=(.*)$') {
        $settings[$matches[1]] = $matches[2].Trim().Trim('"', "'")
    }
}

if (-not $ProjectId) { $ProjectId = $settings['GCP_PROJECT_ID'] }
$geminiKey = $settings['GEMINI_API_KEY']
if (-not $ProjectId -or -not $geminiKey) {
    throw 'Set GCP_PROJECT_ID and GEMINI_API_KEY in .env, or pass -ProjectId.'
}
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw 'Install and authenticate the Google Cloud CLI before deploying.'
}

$region = 'us-west1'
$service = 'csci599-a1'
$repository = 'sportswatch'
$image = "$region-docker.pkg.dev/$ProjectId/$repository/sportswatch-mcp"
$geminiModel = $settings['GEMINI_MODEL']
if (-not $geminiModel) { $geminiModel = 'gemini-3.8-flash' }
$runtimeVars = "GEMINI_API_KEY=$geminiKey,GEMINI_MODEL=$geminiModel"
if ($settings['TAVILY_API_KEY']) {
    $runtimeVars += ",TAVILY_API_KEY=$($settings['TAVILY_API_KEY'])"
}

& gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --project $ProjectId
if ($LASTEXITCODE -ne 0) { throw 'Could not enable Google Cloud APIs.' }

$existingRepository = & gcloud artifacts repositories list --location $region --project $ProjectId --format 'value(name)' | Where-Object { $_ -eq $repository }
if ($LASTEXITCODE -ne 0) { throw 'Could not list Artifact Registry repositories.' }
if (-not $existingRepository) {
    & gcloud artifacts repositories create $repository --repository-format docker --location $region --project $ProjectId
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Artifact Registry repository.' }
}

& gcloud builds submit . --tag $image --project $ProjectId
if ($LASTEXITCODE -ne 0) { throw 'Cloud Build failed.' }

& gcloud run deploy $service --image $image --project $ProjectId --platform managed `
    --region $region --allow-unauthenticated --memory 512Mi --min-instances 0 `
    --max-instances 1 --set-env-vars $runtimeVars
if ($LASTEXITCODE -ne 0) { throw 'Cloud Run deployment failed.' }

$serviceUrl = & gcloud run services describe $service --project $ProjectId --region $region --format 'value(status.url)'
if ($LASTEXITCODE -ne 0 -or -not $serviceUrl) { throw 'Could not read Cloud Run URL.' }
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot '.cloud-run-url'), "$serviceUrl`n")
