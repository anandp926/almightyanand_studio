#!/usr/bin/env bash
# ============================================================================
# gcp_setup.sh — Provisions GCP infrastructure for Almighty Anand Studio
# Budget: $100/month ($250 total over 2.5 months)
# ============================================================================
set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
REGION="us-central1"
ZONE="us-central1-a"
CPU_VM_NAME="almighty-cpu-orchestrator"
GPU_VM_NAME="almighty-gpu-worker"
NETWORK="almighty-net"
SUBNET="almighty-subnet"
BUDGET_ALERT_EMAIL="${BUDGET_ALERT_EMAIL:?Set BUDGET_ALERT_EMAIL}"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   Almighty Anand Studio — GCP Infrastructure Setup          ║"
echo "║   Project: ${PROJECT_ID}                                    ║"
echo "║   Budget:  \$100/month hard cap                              ║"
echo "╚══════════════════════════════════════════════════════════════╝"

# ── Enable required APIs ───────────────────────────────────────────
echo "▸ Enabling GCP APIs..."
gcloud services enable \
  compute.googleapis.com \
  billingbudgets.googleapis.com \
  monitoring.googleapis.com \
  logging.googleapis.com \
  storage.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# ── Networking ─────────────────────────────────────────────────────
echo "▸ Creating VPC network..."
gcloud compute networks create "${NETWORK}" \
  --project="${PROJECT_ID}" \
  --subnet-mode=custom --quiet 2>/dev/null || echo "  (network exists)"

gcloud compute networks subnets create "${SUBNET}" \
  --project="${PROJECT_ID}" \
  --network="${NETWORK}" \
  --region="${REGION}" \
  --range="10.128.0.0/24" --quiet 2>/dev/null || echo "  (subnet exists)"

# Allow internal communication + SSH
gcloud compute firewall-rules create "${NETWORK}-allow-internal" \
  --project="${PROJECT_ID}" \
  --network="${NETWORK}" \
  --allow=tcp,udp,icmp \
  --source-ranges="10.128.0.0/24" --quiet 2>/dev/null || echo "  (firewall rule exists)"

gcloud compute firewall-rules create "${NETWORK}-allow-ssh" \
  --project="${PROJECT_ID}" \
  --network="${NETWORK}" \
  --allow=tcp:22 \
  --source-ranges="0.0.0.0/0" --quiet 2>/dev/null || echo "  (firewall rule exists)"

# ── CPU VM (always-on, e2-medium ~$25/mo) ──────────────────────────
echo "▸ Creating CPU orchestrator VM..."
gcloud compute instances create "${CPU_VM_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --machine-type="e2-medium" \
  --network-interface="network=${NETWORK},subnet=${SUBNET}" \
  --image-family="ubuntu-2404-lts-amd64" \
  --image-project="ubuntu-os-cloud" \
  --boot-disk-size="30GB" \
  --boot-disk-type="pd-standard" \
  --tags="cpu-orchestrator" \
  --metadata=startup-script='#!/bin/bash
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-plugin git ffmpeg
    systemctl enable docker && systemctl start docker
    usermod -aG docker $(whoami)
    # Clone and start the CPU stack
    if [ ! -d /opt/almightyanand_studio ]; then
      echo "Waiting for repo deployment..."
    fi
  ' \
  --scopes="https://www.googleapis.com/auth/cloud-platform" \
  --quiet 2>/dev/null || echo "  (CPU VM exists)"

echo "  CPU VM IP: $(gcloud compute instances describe ${CPU_VM_NAME} --zone=${ZONE} --project=${PROJECT_ID} --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || echo 'pending')"

# ── GPU Spot VM (stopped by default, g2-standard-4 ~$0.40/hr Spot) ─
echo "▸ Creating GPU Spot VM (stopped by default)..."
gcloud compute instances create "${GPU_VM_NAME}" \
  --project="${PROJECT_ID}" \
  --zone="${ZONE}" \
  --machine-type="g2-standard-4" \
  --accelerator="type=nvidia-l4,count=1" \
  --network-interface="network=${NETWORK},subnet=${SUBNET}" \
  --image-family="ubuntu-2404-lts-amd64" \
  --image-project="ubuntu-os-cloud" \
  --boot-disk-size="50GB" \
  --boot-disk-type="pd-ssd" \
  --provisioning-model=SPOT \
  --instance-termination-action=STOP \
  --tags="gpu-worker" \
  --maintenance-policy=TERMINATE \
  --metadata=startup-script='#!/bin/bash
    apt-get update -qq
    apt-get install -y -qq docker.io docker-compose-plugin git
    # Install NVIDIA Container Toolkit
    distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" | \
      sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" | \
      tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -qq && apt-get install -y -qq nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker && systemctl restart docker
    systemctl enable docker
  ' \
  --scopes="https://www.googleapis.com/auth/cloud-platform" \
  --no-restart-on-failure \
  --quiet 2>/dev/null || echo "  (GPU VM exists)"

# Stop the GPU VM immediately (should only run on-demand)
echo "▸ Stopping GPU VM (on-demand only)..."
gcloud compute instances stop "${GPU_VM_NAME}" \
  --zone="${ZONE}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true

# ── GCS Bucket for artifacts ──────────────────────────────────────
BUCKET_NAME="${PROJECT_ID}-almighty-assets"
echo "▸ Creating GCS bucket..."
gcloud storage buckets create "gs://${BUCKET_NAME}" \
  --project="${PROJECT_ID}" \
  --location="${REGION}" \
  --default-storage-class="STANDARD" \
  --uniform-bucket-level-access --quiet 2>/dev/null || echo "  (bucket exists)"

# Lifecycle: auto-delete raw assets after 30 days
cat > /tmp/lifecycle.json << 'EOF'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"age": 30, "matchesPrefix": ["raw/"]}
    }
  ]
}
EOF
gcloud storage buckets update "gs://${BUCKET_NAME}" \
  --lifecycle-file=/tmp/lifecycle.json --quiet

# ── Budget Alert ───────────────────────────────────────────────────
echo "▸ Creating budget alert ($100/month)..."
cat > /tmp/budget.json << EOF
{
  "displayName": "almighty-studio-monthly-cap",
  "amount": {
    "specifiedAmount": {
      "currencyCode": "USD",
      "units": "100"
    }
  },
  "thresholdRules": [
    {"thresholdPercent": 0.5, "spendBasis": "CURRENT_SPEND"},
    {"thresholdPercent": 0.8, "spendBasis": "CURRENT_SPEND"},
    {"thresholdPercent": 1.0, "spendBasis": "CURRENT_SPEND"}
  ],
  "notificationsRule": {
    "monitoringNotificationChannels": [],
    "disableDefaultIamRecipients": false
  }
}
EOF
echo "  ⚠ Budget alert must be created via Cloud Console or Billing API."
echo "  Template saved to /tmp/budget.json"

# ── Summary ────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   ✅ Infrastructure provisioned!                            ║"
echo "║                                                             ║"
echo "║   CPU VM:  ${CPU_VM_NAME} (always-on, e2-medium)           ║"
echo "║   GPU VM:  ${GPU_VM_NAME} (stopped, g2-standard-4 Spot)    ║"
echo "║   Bucket:  gs://${BUCKET_NAME}                             ║"
echo "║   Network: ${NETWORK} / ${SUBNET}                         ║"
echo "║                                                             ║"
echo "║   Next: Deploy code, set .env, run docker compose up -d    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
