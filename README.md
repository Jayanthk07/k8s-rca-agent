# Kubernetes AI Root-Cause Analysis (RCA) Agent

### Objective
This is a working prototype of an AI agent built to investigate Kubernetes incidents and generate root-cause analysis (RCA) reports. It gathers evidence directly from the cluster, evaluates hypotheses, and pinpoints the fundamental cause of failures—all without needing unrestricted cluster access.

---

## Architecture & Agent Design

* **Agent Framework:** The agent uses a single-pass inference loop powered by the Gemini 3.6 Flash model. I considered agentic frameworks like LangGraph for multi-step reasoning, but a consolidated state-snapshot approach proved more reliable and efficient for log-based and event-based anomaly detection.
* **Observability & Tooling:** The agent interacts with the Kubernetes API (via the Python Kubernetes client) to dynamically query pod states, events, and container logs, alongside network states like Services and Endpoints.
* **Hypothesis Evaluation:** The system parses raw cluster data, forms hypotheses (e.g., Application Error vs. Infrastructure Exhaustion), and matches event warnings against log termination patterns to synthesize the final RCA.
* **Deployment Architecture:** You can execute the agent manually for local development, or package it into a Docker container and deploy it as an autonomous Kubernetes `CronJob` for continuous in-cluster monitoring.

---

## Security & Sandboxing Model

Sandboxing was a strict requirement for this project. The agent operates within a tight security boundary to prevent prompt injections and unauthorized cluster manipulation.

* **Read-Only RBAC Constraint:** The architecture is designed around read-only investigation. The agent operates under a dedicated ServiceAccount (`rca-agent-sa`). Its `ClusterRole` explicitly limits verbs to `get`, `list`, and `watch`. It is fundamentally unable to execute commands (`exec`), delete resources, or escalate privileges.
* **Untrusted Data Sanitization:** Logs, Kubernetes resources, and application traces are treated as untrusted input. I implemented a regex-based sanitization pipeline (`sanitize_untrusted_data`) that strips known prompt injection vectors (e.g., `ignore previous instructions`, `rm -rf`) before the data reaches the LLM context window.
* **Credential Management:** API keys are never hardcoded. In production, credentials are securely injected at runtime using Kubernetes `Secret` resources mapped to environment variables.

---

## Reproducible Setup (Local Developer Mode)

**Prerequisites:**
* Linux / WSL2 Environment
* `kind` (Kubernetes in Docker)
* `kubectl` & `helm`
* `python3` (with `google-genai` and `kubernetes` packages)

### 1. Cluster Provisioning
Spin up the local `kind` cluster and deploy the Metrics Server:
```bash
./scripts/01-setup-cluster.sh
```

### 2. Apply the Security Sandbox
Enforce the read-only RBAC constraints for the agent:
```bash
kubectl apply -f k8s/rbac/agent-readonly.yaml
```

### 3. Deploy a Failure Scenario
Deploy an inherently faulty application (e.g., a memory leak scenario):
```bash
kubectl apply -f k8s/scenarios/01-oom-kill.yaml
```

### 4. Run the Agent
Export your API key as an environment variable and run the script locally:
```bash
export GEMINI_API_KEY="YOUR_GEMINI_API_KEY"
python3 single_agent.py
```

---

## Production Deployment (In-Cluster CronJob)

To run the agent autonomously inside the cluster, package it using Docker and deploy the Kubernetes CronJob.

**1. Build and side-load the Docker image:**
```bash
docker build -t rca-agent:latest .
kind load docker-image rca-agent:latest
```

**2. Inject the Gemini API Key into the cluster vault:**
```bash
kubectl create secret generic gemini-secret --from-literal=api-key="YOUR_GEMINI_API_KEY"
```

**3. Deploy the autonomous CronJob:**
```bash
kubectl apply -f k8s/deploy/agent-cronjob.yaml
```

---

## Testing & Security Validation

### Proof of Blocked Action (RBAC Sandbox)
To verify the RBAC constraints are actively blocking unauthorized actions, we can test the `rca-agent-sa` ServiceAccount's permissions directly:

```bash
$ kubectl auth can-i delete pods --as=system:serviceaccount:default:rca-agent-sa
no

$ kubectl auth can-i get pods --as=system:serviceaccount:default:rca-agent-sa
yes
```

### Prompt Injection & Security Unit Tests
The input sanitization filter is validated against adversarial payloads via an automated `pytest` suite:

```text
$ pytest test_agent.py -v
======================================================== test session starts =========================================================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.6.0 -- /home/jayanth/Documents/k8s-rca-agent/venv/bin/python3
cachedir: .pytest_cache
rootdir: /home/jayanth/Documents/k8s-rca-agent
plugins: anyio-4.15.1
collected 4 items                                                                                                                     

test_agent.py::test_sanitize_prompt_injection PASSED                                                                           [ 25%]
test_agent.py::test_sanitize_destructive_commands PASSED                                                                       [ 50%]
test_agent.py::test_sanitize_empty_input PASSED                                                                                [ 75%]
test_agent.py::test_sanitize_length_limit PASSED                                                                               [100%]
```

---

## Example Agent Output

Below is a sample RCA report generated by the agent during the `OOMKilled` scenario, formatted strictly to standard incident response guidelines.

```text
==================================================
ROOT CAUSE ANALYSIS REPORT
==================================================

## Root Cause
Unbounded Memory Allocation in payment-processor: The payment-processor-dfdbf8cb-9pmgt application continuously allocates memory without releasing it, leading to container termination (OOMKilled / memory limit violation) and subsequent container restarts.

## Timeline of Events
* T-00:05: Pod payment-processor-dfdbf8cb-9pmgt scheduled to rca-cluster-control-plane.
* T-00:03: Container starts and process begins logging "Allocating memory..." repeatedly.
* T-00:01: Process abruptly terminates without a clean shutdown sequence.
* T-00:00: Kubernetes registers container failure and initiates a restart (Restart Count: 1).

## Observed Facts
* Pod payment-processor-dfdbf8cb-9pmgt has restarted 1 time and is currently in Running state.
* The previous and current execution logs for the pod show the process repeatedly logging "Allocating memory..." until it abruptly terminates.
* The host node rca-cluster-control-plane is reported as NodeReady with NodeHasSufficientMemory, NodeHasNoDiskPressure, and NodeHasSufficientPID.

## Hypotheses Evaluated
* Node Resource Exhaustion (Memory/Disk/PID Pressure): Ruled Out. Control plane events explicitly confirm the node has sufficient memory, no disk pressure, sufficient PIDs, and is in state NodeReady.
* Application killed due to Out Of Memory (OOM) / Exceeding Container Limits: Confirmed. Application logs for both the current and previous container instances end abruptly after consecutive memory allocation steps, which is characteristic of an OOM kill by the Linux cgroup.

## Supporting Evidence
* Crash Logs: Identical current and previous logs showing repeated "Allocating memory..." prior to abrupt termination.
* Pod State: payment-processor-dfdbf8cb-9pmgt explicitly shows "restarts": 1.

## Confidence Level
High (95%)
```