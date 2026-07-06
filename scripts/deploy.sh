#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh — Enterprise On-Prem LLM Platform POC Deployment Script
# =============================================================================
# Usage:
#   ./scripts/deploy.sh               # Full deployment
#   ./scripts/deploy.sh --dry-run     # Print commands without executing
#   ./scripts/deploy.sh --uninstall   # Remove release and namespace
#
# Requirements: kubectl, helm ≥3, cluster access via KUBECONFIG
# Supported distributions: k3s, kind, minikube
#
# NOTE: Distribution-specific steps (e.g., minikube tunnel for LoadBalancer
# ingress, kind port-mapping) are noted in comments but not automated here.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RELEASE_NAME="llm-poc"
NAMESPACE="llm-poc"
MONITORING_NAMESPACE="monitoring"
INGRESS_NAMESPACE="ingress-nginx"
SERVICE_ACCOUNT="llm-platform"
SECRET_NAME="llm-poc-secrets"

CHART_DIR="${REPO_ROOT}/llm-platform"
VALUES_FILE="${CHART_DIR}/values-poc.yaml"
LOCAL_VALUES_FILE="${CHART_DIR}/values-poc-local.yaml"
OBSERVABILITY_CHART="${CHART_DIR}/charts/observability"

INGRESS_MANIFEST="https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml"

MIN_CPU=8
MIN_RAM_GI=16

DRY_RUN=false
UNINSTALL=false

TOTAL_START=$(date +%s)

# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
fail()    { echo -e "${RED}[FAIL]${RESET}  $*" >&2; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
for arg in "$@"; do
  case "${arg}" in
    --dry-run)   DRY_RUN=true ;;
    --uninstall) UNINSTALL=true ;;
    --help|-h)
      echo "Usage: $0 [--dry-run] [--uninstall]"
      echo ""
      echo "  --dry-run    Print kubectl/helm commands without executing them."
      echo "  --uninstall  Uninstall the Helm release and delete the namespace."
      exit 0
      ;;
    *)
      fail "Unknown argument: ${arg}"
      echo "Run '$0 --help' for usage." >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Command executor — respects --dry-run
# ---------------------------------------------------------------------------
run() {
  if "${DRY_RUN}"; then
    echo -e "${YELLOW}[DRY-RUN]${RESET} $*"
  else
    "$@"
  fi
}

# ---------------------------------------------------------------------------
# Elapsed-time helper
# ---------------------------------------------------------------------------
elapsed() {
  local start=$1
  local end
  end=$(date +%s)
  echo $(( end - start ))
}

# ---------------------------------------------------------------------------
# --uninstall path
# ---------------------------------------------------------------------------
if "${UNINSTALL}"; then
  echo -e "${BOLD}=== Uninstalling LLM Platform POC ===${RESET}"

  info "Uninstalling Helm release '${RELEASE_NAME}' from namespace '${NAMESPACE}'..."
  run helm uninstall "${RELEASE_NAME}" --namespace "${NAMESPACE}" \
    || warn "Helm release '${RELEASE_NAME}' not found or already removed."

  info "Uninstalling Helm release 'observability' from namespace '${MONITORING_NAMESPACE}'..."
  run helm uninstall observability --namespace "${MONITORING_NAMESPACE}" \
    || warn "Helm release 'observability' not found or already removed."

  info "Deleting namespace '${NAMESPACE}'..."
  run kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true

  info "Deleting namespace '${MONITORING_NAMESPACE}'..."
  run kubectl delete namespace "${MONITORING_NAMESPACE}" --ignore-not-found=true

  success "Uninstall complete. Cluster is clean."
  exit 0
fi

# ===========================================================================
# DEPLOY PATH
# ===========================================================================
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   Enterprise On-Prem LLM Platform — POC Deployment          ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
if "${DRY_RUN}"; then
  warn "DRY-RUN MODE — commands will be printed but NOT executed."
  echo ""
fi

# ===========================================================================
# STEP 1 — Preflight: verify kubectl and helm ≥3
# ===========================================================================
STEP=1
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Preflight checks ---${RESET}"

# Check kubectl
if ! command -v kubectl &>/dev/null; then
  fail "Step ${STEP}: 'kubectl' not found on PATH."
  echo "" >&2
  echo "  Install kubectl:" >&2
  echo "    Linux/Mac:  https://kubernetes.io/docs/tasks/tools/" >&2
  echo "    Windows:    https://kubernetes.io/docs/tasks/tools/install-kubectl-windows/" >&2
  exit 1
fi
info "kubectl found: $(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1)"

# Check helm and version ≥3
if ! command -v helm &>/dev/null; then
  fail "Step ${STEP}: 'helm' not found on PATH."
  echo "" >&2
  echo "  Install Helm ≥3:" >&2
  echo "    curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash" >&2
  echo "    Or: https://helm.sh/docs/intro/install/" >&2
  exit 1
fi

HELM_VERSION=$(helm version --short 2>/dev/null | grep -oE 'v[0-9]+' | head -1 | tr -d 'v' || echo "0")
if [[ "${HELM_VERSION}" -lt 3 ]]; then
  fail "Step ${STEP}: helm version ≥3 is required (found: $(helm version --short 2>/dev/null))."
  echo "" >&2
  echo "  Upgrade Helm: https://helm.sh/docs/intro/install/" >&2
  exit 1
fi
info "helm found: $(helm version --short 2>/dev/null)"

# Verify cluster is reachable
if ! "${DRY_RUN}"; then
  if ! kubectl cluster-info &>/dev/null; then
    fail "Step ${STEP}: Cannot reach the Kubernetes cluster. Check KUBECONFIG and cluster status."
    exit 1
  fi
  info "Cluster reachable: $(kubectl cluster-info 2>/dev/null | head -1)"
fi

success "Step ${STEP} complete (${elapsed "${STEP_START}"}s)"
echo ""

# ===========================================================================
# STEP 2 — Cluster capacity check (≥8 CPU, ≥16Gi RAM)
# ===========================================================================
STEP=2
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Cluster capacity check ---${RESET}"

if ! "${DRY_RUN}"; then
  # Parse allocatable CPU and memory from all nodes
  NODE_JSON=$(kubectl get nodes -o json 2>/dev/null) \
    || { fail "Step ${STEP}: Failed to query cluster nodes."; exit 1; }

  # Sum allocatable CPU cores (handles millicores like "8000m" and whole cores like "8")
  TOTAL_CPU_MILLI=0
  while IFS= read -r cpu_val; do
    if [[ "${cpu_val}" =~ ^([0-9]+)m$ ]]; then
      TOTAL_CPU_MILLI=$(( TOTAL_CPU_MILLI + ${BASH_REMATCH[1]} ))
    elif [[ "${cpu_val}" =~ ^([0-9]+)$ ]]; then
      TOTAL_CPU_MILLI=$(( TOTAL_CPU_MILLI + ${BASH_REMATCH[1]} * 1000 ))
    fi
  done < <(echo "${NODE_JSON}" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); \
      [print(n['status']['allocatable'].get('cpu','0')) \
       for n in d.get('items',[]) \
       if n.get('status',{}).get('allocatable')]" 2>/dev/null || true)

  TOTAL_CPU=$(( TOTAL_CPU_MILLI / 1000 ))

  # Sum allocatable memory in MiB (handles Ki, Mi, Gi suffixes)
  TOTAL_MEM_MI=0
  while IFS= read -r mem_val; do
    if [[ "${mem_val}" =~ ^([0-9]+)Ki$ ]]; then
      TOTAL_MEM_MI=$(( TOTAL_MEM_MI + ${BASH_REMATCH[1]} / 1024 ))
    elif [[ "${mem_val}" =~ ^([0-9]+)Mi$ ]]; then
      TOTAL_MEM_MI=$(( TOTAL_MEM_MI + ${BASH_REMATCH[1]} ))
    elif [[ "${mem_val}" =~ ^([0-9]+)Gi$ ]]; then
      TOTAL_MEM_MI=$(( TOTAL_MEM_MI + ${BASH_REMATCH[1]} * 1024 ))
    elif [[ "${mem_val}" =~ ^([0-9]+)$ ]]; then
      # raw bytes
      TOTAL_MEM_MI=$(( TOTAL_MEM_MI + ${BASH_REMATCH[1]} / 1048576 ))
    fi
  done < <(echo "${NODE_JSON}" | \
    python3 -c "import sys,json; d=json.load(sys.stdin); \
      [print(n['status']['allocatable'].get('memory','0')) \
       for n in d.get('items',[]) \
       if n.get('status',{}).get('allocatable')]" 2>/dev/null || true)

  TOTAL_MEM_GI=$(( TOTAL_MEM_MI / 1024 ))

  info "Detected allocatable capacity: ${TOTAL_CPU} CPU cores, ${TOTAL_MEM_GI}Gi RAM"

  CAPACITY_OK=true
  if [[ "${TOTAL_CPU}" -lt "${MIN_CPU}" ]]; then
    warn "CPU below minimum: detected ${TOTAL_CPU} cores, required ${MIN_CPU} cores."
    CAPACITY_OK=false
  fi
  if [[ "${TOTAL_MEM_GI}" -lt "${MIN_RAM_GI}" ]]; then
    warn "RAM below minimum: detected ${TOTAL_MEM_GI}Gi, required ${MIN_RAM_GI}Gi."
    CAPACITY_OK=false
  fi

  if ! "${CAPACITY_OK}"; then
    warn "Cluster capacity is below the POC minimum (${MIN_CPU} CPU / ${MIN_RAM_GI}Gi RAM)."
    warn "The Ollama container alone requests 1 CPU / 8Gi RAM. Some pods may remain Pending."
    echo ""
    read -r -p "Continue anyway? [y/N] " CONFIRM
    if [[ ! "${CONFIRM}" =~ ^[Yy]$ ]]; then
      info "Deployment cancelled by user."
      exit 0
    fi
  else
    success "Cluster meets minimum capacity requirements (${TOTAL_CPU} CPU / ${TOTAL_MEM_GI}Gi RAM)."
  fi
else
  info "[DRY-RUN] Skipping live cluster capacity check."
fi

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 3 — Install NGINX Ingress Controller
# ===========================================================================
STEP=3
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Install NGINX Ingress Controller ---${RESET}"

info "Applying NGINX Ingress Controller manifest..."
run kubectl apply -f "${INGRESS_MANIFEST}" \
  || { fail "Step ${STEP}: Failed to apply NGINX Ingress Controller manifest."; exit 1; }

if ! "${DRY_RUN}"; then
  info "Waiting for NGINX Ingress Controller pod to become Ready (timeout: 120s)..."
  kubectl wait \
    --namespace "${INGRESS_NAMESPACE}" \
    --for=condition=ready pod \
    --selector=app.kubernetes.io/component=controller \
    --timeout=120s \
    || { fail "Step ${STEP}: NGINX Ingress Controller did not become Ready within 120s."; exit 1; }
  success "NGINX Ingress Controller is Ready."
else
  echo -e "${YELLOW}[DRY-RUN]${RESET} kubectl wait --namespace ${INGRESS_NAMESPACE} --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s"
fi

# NOTE — Distribution-specific ingress access:
#   k3s:     Use the node's external IP; LoadBalancer is provisioned by ServiceLB (klipper).
#   kind:     Use 'kubectl port-forward -n ingress-nginx svc/ingress-nginx-controller 80:80'
#             or configure kind with extraPortMappings in the cluster config.
#   minikube: Run 'minikube tunnel' in a separate terminal to expose LoadBalancer services.

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 4 — Create namespace 'llm-poc' (idempotent)
# ===========================================================================
STEP=4
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Create namespace '${NAMESPACE}' ---${RESET}"

info "Creating namespace '${NAMESPACE}' (idempotent)..."
run bash -c "kubectl create namespace ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -" \
  || { fail "Step ${STEP}: Failed to create namespace '${NAMESPACE}'."; exit 1; }

# Ensure the required metadata label is present (for NetworkPolicy namespaceSelector)
if ! "${DRY_RUN}"; then
  run kubectl label namespace "${NAMESPACE}" \
    "kubernetes.io/metadata.name=${NAMESPACE}" \
    --overwrite=true \
    || { fail "Step ${STEP}: Failed to label namespace '${NAMESPACE}'."; exit 1; }
  success "Namespace '${NAMESPACE}' exists with label kubernetes.io/metadata.name=${NAMESPACE}."
else
  echo -e "${YELLOW}[DRY-RUN]${RESET} kubectl label namespace ${NAMESPACE} kubernetes.io/metadata.name=${NAMESPACE} --overwrite=true"
fi

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 5 — Create Secret and ServiceAccount (idempotent)
# ===========================================================================
STEP=5
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Create Secret '${SECRET_NAME}' and ServiceAccount '${SERVICE_ACCOUNT}' ---${RESET}"

info "Creating Secret '${SECRET_NAME}' (idempotent)..."
run bash -c "kubectl create secret generic ${SECRET_NAME} \
  --namespace ${NAMESPACE} \
  --from-literal=GATEWAY_API_KEY=poc-secret-key \
  --from-literal=REDIS_PASSWORD='' \
  --from-literal=AUDIT_API_KEY=poc-audit-key \
  --dry-run=client -o yaml | kubectl apply -f -" \
  || { fail "Step ${STEP}: Failed to create Secret '${SECRET_NAME}'."; exit 1; }

info "Creating ServiceAccount '${SERVICE_ACCOUNT}' (idempotent)..."
run bash -c "kubectl create serviceaccount ${SERVICE_ACCOUNT} \
  --namespace ${NAMESPACE} \
  --dry-run=client -o yaml | kubectl apply -f -" \
  || { fail "Step ${STEP}: Failed to create ServiceAccount '${SERVICE_ACCOUNT}'."; exit 1; }

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 6 — Helm dependency update
# ===========================================================================
STEP=6
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Helm dependency update ---${RESET}"

info "Running 'helm dependency update ${CHART_DIR}'..."
run helm dependency update "${CHART_DIR}" \
  || { fail "Step ${STEP}: 'helm dependency update' failed. Check chart dependencies and network access."; exit 1; }

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 7 — Helm upgrade --install (umbrella chart)
# ===========================================================================
STEP=7
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Helm install/upgrade umbrella chart ---${RESET}"

info "Running 'helm upgrade --install ${RELEASE_NAME}'..."
run helm upgrade --install "${RELEASE_NAME}" "${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  --values "${VALUES_FILE}" \
  --values "${LOCAL_VALUES_FILE}" \
  --wait \
  --timeout 15m \
  || { fail "Step ${STEP}: 'helm upgrade --install' failed. Run 'helm status ${RELEASE_NAME} -n ${NAMESPACE}' for details."; exit 1; }

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 8 — Helm upgrade --install observability (monitoring namespace)
# ===========================================================================
STEP=8
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Helm install/upgrade observability chart ---${RESET}"

info "Running 'helm upgrade --install observability' into namespace '${MONITORING_NAMESPACE}'..."
run helm upgrade --install observability "${OBSERVABILITY_CHART}" \
  --namespace "${MONITORING_NAMESPACE}" \
  --create-namespace \
  --timeout 10m \
  || { fail "Step ${STEP}: Observability chart install failed. Check kube-prometheus-stack dependency."; exit 1; }

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 9 — kubectl rollout status for all Deployments in llm-poc
# ===========================================================================
STEP=9
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Wait for Deployment rollouts ---${RESET}"

if ! "${DRY_RUN}"; then
  DEPLOYMENTS=$(kubectl get deployments -n "${NAMESPACE}" -o name 2>/dev/null) \
    || { fail "Step ${STEP}: Failed to list Deployments in namespace '${NAMESPACE}'."; exit 1; }

  if [[ -z "${DEPLOYMENTS}" ]]; then
    warn "No Deployments found in namespace '${NAMESPACE}'. Skipping rollout wait."
  else
    ALL_ROLLED_OUT=true
    while IFS= read -r deployment; do
      info "Waiting for rollout: ${deployment}..."
      if ! kubectl rollout status "${deployment}" \
          --namespace "${NAMESPACE}" \
          --timeout=300s; then
        fail "Step ${STEP}: Rollout timed out for ${deployment}."
        ALL_ROLLED_OUT=false
      fi
    done <<< "${DEPLOYMENTS}"

    if ! "${ALL_ROLLED_OUT}"; then
      fail "Step ${STEP}: One or more Deployments did not roll out within the timeout."
      exit 1
    fi
    success "All Deployments in '${NAMESPACE}' are Available."
  fi
else
  echo -e "${YELLOW}[DRY-RUN]${RESET} # For each deployment in ${NAMESPACE}:"
  echo -e "${YELLOW}[DRY-RUN]${RESET} kubectl rollout status <deployment> --namespace ${NAMESPACE} --timeout=300s"
fi

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 10 — Check for Pending pods (warning only, not a failure)
# ===========================================================================
STEP=10
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Check for Pending pods ---${RESET}"

if ! "${DRY_RUN}"; then
  PENDING_PODS=$(kubectl get pods -n "${NAMESPACE}" \
    --field-selector=status.phase=Pending \
    --no-headers \
    -o custom-columns="NAME:.metadata.name" 2>/dev/null || true)

  if [[ -n "${PENDING_PODS}" ]]; then
    warn "The following pods are Pending (likely due to insufficient cluster resources):"
    while IFS= read -r pod; do
      warn "  → ${pod}"
    done <<< "${PENDING_PODS}"
    warn "Check node capacity or scale the cluster. Deployment continues (this is a warning, not a failure)."
  else
    success "No Pending pods detected in namespace '${NAMESPACE}'."
  fi
else
  echo -e "${YELLOW}[DRY-RUN]${RESET} kubectl get pods -n ${NAMESPACE} --field-selector=status.phase=Pending"
fi

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# STEP 11 — Wait for Ollama model-pull init Job
# ===========================================================================
STEP=11
STEP_START=$(date +%s)
echo -e "${BOLD}--- Step ${STEP}: Wait for Ollama model pre-pull Job ---${RESET}"

OLLAMA_JOB="${RELEASE_NAME}-inference-ollama-model-pull"

if ! "${DRY_RUN}"; then
  if kubectl get job "${OLLAMA_JOB}" --namespace "${NAMESPACE}" &>/dev/null; then
    info "Waiting for Ollama model-pull Job '${OLLAMA_JOB}' to complete (timeout: 6600s ~110min)..."
    info "This downloads model weights and may take a long time on first run."

    # Stream Job pod logs for progress visibility
    JOB_POD=$(kubectl get pods -n "${NAMESPACE}" \
      --selector="job-name=${OLLAMA_JOB}" \
      --no-headers \
      -o custom-columns="NAME:.metadata.name" 2>/dev/null | head -1 || true)

    if [[ -n "${JOB_POD}" ]]; then
      info "Streaming model pull progress from pod '${JOB_POD}'..."
      kubectl logs -n "${NAMESPACE}" "${JOB_POD}" -f --ignore-errors=true &
      LOG_PID=$!
    fi

    if kubectl wait "job/${OLLAMA_JOB}" \
        --namespace "${NAMESPACE}" \
        --for=condition=complete \
        --timeout=6600s; then
      success "Ollama model-pull Job completed successfully."
    else
      # Kill background log tail if running
      kill "${LOG_PID:-}" 2>/dev/null || true
      fail "Step ${STEP}: Ollama model-pull Job did not complete within timeout (6600s)."
      fail "Check Job status: kubectl describe job/${OLLAMA_JOB} -n ${NAMESPACE}"
      exit 1
    fi

    # Kill background log tail
    kill "${LOG_PID:-}" 2>/dev/null || true
  else
    info "Job '${OLLAMA_JOB}' not found — models.preload may be empty or initJob.enabled=false. Skipping."
  fi
else
  echo -e "${YELLOW}[DRY-RUN]${RESET} kubectl wait job/${OLLAMA_JOB} --namespace ${NAMESPACE} --for=condition=complete --timeout=6600s"
fi

success "Step ${STEP} complete ($(elapsed "${STEP_START}")s)"
echo ""

# ===========================================================================
# Summary
# ===========================================================================
TOTAL_ELAPSED=$(elapsed "${TOTAL_START}")

echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║   Deployment Complete ✓                                      ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Release:    ${GREEN}${RELEASE_NAME}${RESET}"
echo -e "  Namespace:  ${GREEN}${NAMESPACE}${RESET}"
echo -e "  Total time: ${BOLD}${TOTAL_ELAPSED}s${RESET}"
echo ""
echo -e "  ${BOLD}Access URLs (add to /etc/hosts):${RESET}"
echo -e "    <cluster-ip>  llm-poc.local"
echo -e "    <cluster-ip>  llm-portal.local"
echo -e "    <cluster-ip>  grafana-poc.local"
echo ""
echo -e "  ${BOLD}Determine <cluster-ip>:${RESET}"
echo -e "    k3s:      kubectl get svc -n ingress-nginx ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'"
echo -e "    kind:     kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type==\"InternalIP\")].address}'"
echo -e "    minikube: minikube ip"
echo ""
echo -e "  ${BOLD}Validate with:${RESET}"
echo -e "    ./scripts/smoke-test.sh"
echo ""
