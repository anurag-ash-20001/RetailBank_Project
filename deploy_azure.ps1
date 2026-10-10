# deploy_azure.ps1 — provision Azure resources and deploy:
#   * the pipeline as a Container Apps Job that reads/writes Azure Blob Storage
#   * the Streamlit dashboard as a Container App (scales to zero when idle)
#
# Images are built by GitHub Actions (.github/workflows/build-images.yml) and
# pulled from ghcr.io — Azure for Students blocks ACR cloud builds.
#
# Prereqs: Azure CLI installed, `az login` done, images published to ghcr.io (public).
#
# Usage:   .\deploy_azure.ps1 -Location uaenorth
#          .\deploy_azure.ps1 -Location uaenorth -ImageTag <git sha>   # pin a build
# Re-running is safe: existing resources are reused and updated to the image tag.
param(
    [string]$ResourceGroup = "rg-retailbank",
    [string]$Location      = "uaenorth",
    [string]$Registry      = "ghcr.io/anurag-ash-20001",
    [string]$ImageTag      = "latest",
    [string]$Suffix        = ""          # set to reuse names from an earlier run
)

function Run([string]$what, [scriptblock]$cmd) {
    Write-Host "==> $what" -ForegroundColor Cyan
    $out = & $cmd
    if ($LASTEXITCODE -ne 0) { throw "Failed: $what" }
    return $out
}

$sub = Run "Checking login" { az account show --query id -o tsv }

# Stable unique suffix per subscription + resource group (storage names are global)
if (-not $Suffix) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("$sub/$ResourceGroup"))
    $Suffix = (($hash[0..3] | ForEach-Object { $_.ToString("x2") }) -join "")
}
$Storage   = "retailbank$Suffix"
$Identity  = "id-retailbank"
$Env       = "cae-retailbank"
$Job       = "retailbank-pipeline"
$App       = "retailbank-dashboard"
$JobImage  = "$Registry/retailbank-pipeline:$ImageTag"
$AppImage  = "$Registry/retailbank-dashboard:$ImageTag"

Run "Installing containerapp CLI extension" { az extension add --name containerapp --upgrade --only-show-errors }
foreach ($ns in "Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.Storage", "Microsoft.ManagedIdentity") {
    Run "Registering provider $ns" { az provider register --namespace $ns --wait --only-show-errors }
}

Run "Resource group $ResourceGroup" { az group create -n $ResourceGroup -l $Location -o none }

# ---- Storage -------------------------------------------------------------
Run "Storage account $Storage" {
    az storage account create -g $ResourceGroup -n $Storage -l $Location --sku Standard_LRS `
        --kind StorageV2 --min-tls-version TLS1_2 --allow-blob-public-access false -o none }
$storageId  = az storage account show -g $ResourceGroup -n $Storage --query id -o tsv
$storageUrl = az storage account show -g $ResourceGroup -n $Storage --query primaryEndpoints.blob -o tsv

# Let the signed-in user upload/download blobs with their Azure AD login
$me = Run "Looking up signed-in user" { az ad signed-in-user show --query id -o tsv }
az role assignment create --assignee-object-id $me --assignee-principal-type User `
    --role "Storage Blob Data Contributor" --scope $storageId -o none 2>$null

# Role assignments can take a minute or two to take effect, so retry
foreach ($c in "raw", "output") {
    Write-Host "==> Blob container $c" -ForegroundColor Cyan
    for ($i = 1; $i -le 12; $i++) {
        az storage container create --account-name $Storage -n $c --auth-mode login -o none 2>$null
        if ($LASTEXITCODE -eq 0) { break }
        if ($i -eq 12) { throw "Failed: Blob container $c (storage permission not active yet - re-run the script in a few minutes)" }
        Write-Host "    waiting for storage permission to take effect..."
        Start-Sleep -Seconds 15
    }
}

# ---- Managed identity + permissions --------------------------------------
Run "Managed identity $Identity" { az identity create -g $ResourceGroup -n $Identity -l $Location -o none }
$idId        = az identity show -g $ResourceGroup -n $Identity --query id -o tsv
$idPrincipal = az identity show -g $ResourceGroup -n $Identity --query principalId -o tsv
$idClient    = az identity show -g $ResourceGroup -n $Identity --query clientId -o tsv

az role assignment create --assignee-object-id $idPrincipal --assignee-principal-type ServicePrincipal `
    --role "Storage Blob Data Contributor" --scope $storageId -o none 2>$null

# ---- Container Apps environment ------------------------------------------
$envExists = az containerapp env show -g $ResourceGroup -n $Env --query id -o tsv 2>$null
if (-not $envExists) {
    Run "Container Apps environment $Env (takes a few minutes)" {
        az containerapp env create -g $ResourceGroup -n $Env -l $Location -o none }
}

# ---- Pipeline job --------------------------------------------------------
$jobExists = az containerapp job show -g $ResourceGroup -n $Job --query id -o tsv 2>$null
if ($jobExists) {
    Run "Updating job $Job" { az containerapp job update -g $ResourceGroup -n $Job --image $JobImage -o none }
} else {
    Run "Creating job $Job" {
        az containerapp job create -g $ResourceGroup -n $Job --environment $Env `
            --trigger-type Manual --replica-timeout 1800 --replica-retry-limit 0 `
            --parallelism 1 --replica-completion-count 1 `
            --image $JobImage --cpu 1.0 --memory 2.0Gi --mi-user-assigned $idId `
            --env-vars "AZURE_STORAGE_ACCOUNT_URL=$storageUrl" "AZURE_CLIENT_ID=$idClient" -o none }
}

# ---- Dashboard (Streamlit web app, scales to zero when idle) -------------
$appExists = az containerapp show -g $ResourceGroup -n $App --query id -o tsv 2>$null
if ($appExists) {
    Run "Updating dashboard $App" {
        az containerapp update -g $ResourceGroup -n $App --image $AppImage `
            --revision-suffix ("r" + (Get-Date -Format "MMddHHmmss")) -o none }
} else {
    Run "Creating dashboard $App" {
        az containerapp create -g $ResourceGroup -n $App --environment $Env `
            --image $AppImage --cpu 0.5 --memory 1.0Gi `
            --ingress external --target-port 8501 --min-replicas 0 --max-replicas 1 `
            --user-assigned $idId `
            --env-vars "AZURE_STORAGE_ACCOUNT_URL=$storageUrl" "AZURE_CLIENT_ID=$idClient" -o none }
}
$appUrl = az containerapp show -g $ResourceGroup -n $App --query properties.configuration.ingress.fqdn -o tsv

Write-Host ""
Write-Host "Deployed." -ForegroundColor Green
Write-Host "  Dashboard       : https://$appUrl"
Write-Host "  Storage account : $Storage   (containers: raw, output)"
Write-Host "  Job             : $Job   (resource group $ResourceGroup)"
Write-Host ""
Write-Host "Upload input data:"
Write-Host "  az storage blob upload-batch --account-name $Storage -d raw/RetailBank_SourceData -s RetailBank_SourceData --auth-mode login --overwrite"
Write-Host "  az storage blob upload-batch --account-name $Storage -d raw/RetailBank_SourceData_Day2 -s RetailBank_SourceData_Day2 --auth-mode login --overwrite"
Write-Host "Run the pipeline:"
Write-Host "  az containerapp job start -g $ResourceGroup -n $Job"
