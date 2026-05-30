param(
    [Parameter(Mandatory=$true)]
    [string]$VM_IP,
    [Parameter(Mandatory=$true)]
    [string]$SSH_KEY
)

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Deploying MEXC Bot to Oracle Cloud VM" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

$BOT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# Step 1: Create project directory on VM
Write-Host "[1/4] Creating directory on VM..." -ForegroundColor Yellow
ssh -i $SSH_KEY -o StrictHostKeyChecking=no ubuntu@$VM_IP "mkdir -p /home/ubuntu/mexc-bot"

# Step 2: Copy all bot files (excluding .venv, __pycache__, etc.)
Write-Host "[2/4] Copying bot files to VM..." -ForegroundColor Yellow
scp -i $SSH_KEY -r `
    "$BOT_DIR\backend" `
    "$BOT_DIR\deploy" `
    "$BOT_DIR\static" `
    "$BOT_DIR\.env" `
    "$BOT_DIR\requirements.txt" `
    "$BOT_DIR\run.py" `
    ubuntu@$VM_IP:/home/ubuntu/mexc-bot/

# Step 3: Run setup script on VM
Write-Host "[3/4] Running setup on VM..." -ForegroundColor Yellow
ssh -i $SSH_KEY ubuntu@$VM_IP "bash /home/ubuntu/mexc-bot/deploy/setup_vm.sh"

# Step 4: Update .env with MEXC API keys (already copied via SCP)
Write-Host "[4/4] Verifying bot is running..." -ForegroundColor Yellow
Start-Sleep -Seconds 5
ssh -i $SSH_KEY ubuntu@$VM_IP "sudo systemctl status mexc-bot --no-pager | head -10"

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Dashboard: http://$VM_IP`:8000" -ForegroundColor Cyan
Write-Host ""
Write-Host "To check logs later:" -ForegroundColor Gray
Write-Host "  ssh -i $SSH_KEY ubuntu@$VM_IP 'journalctl -u mexc-bot -f'" -ForegroundColor Gray
Write-Host ""
Write-Host "To restart the bot:" -ForegroundColor Gray
Write-Host "  ssh -i $SSH_KEY ubuntu@$VM_IP 'sudo systemctl restart mexc-bot'" -ForegroundColor Gray
Write-Host ""
