# Kubernetes AI Root-Cause Analysis (RCA) Agent

## Objective
A working prototype of an AI agent capable of investigating Kubernetes incidents and producing a root-cause analysis (RCA). The agent gathers evidence directly from the cluster, forms hypotheses, and determines the fundamental cause of distributed failures without unrestricted cluster access.

## Architecture & Agent Design
*   **Agent Framework:** The agent uses a single-pass inference loop powered by the Gemini 3.6 Flash model. While agentic frameworks like LangGraph were considered for multi-step reasoning, a consolidated state-snapshot approach proved more efficient for log-based and event-based anomaly detection.
*   **Observability & Tooling:** The agent interacts with the Kubernetes API (via the Python equivalent of `client-go`) to dynamically query pod states, events, and container logs. 
*   **Hypothesis Evaluation:** The system parses raw cluster data, forms hypotheses (e.g., Application Error vs. Infrastructure Exhaustion), and matches event warnings against log termination patterns to synthesize the final RCA.

## Security & Sandboxing Model
Sandboxing is a mandatory requirement for this prototype[cite: 1]. The agent operates within a strictly controlled security boundary to prevent prompt injections and unauthorized cluster manipulation.
1.  **Read-Only RBAC Constraint:** The initial prototype should be designed around read-only investigation[cite: 1]. The agent operates under a dedicated `ServiceAccount` (`rca-agent-sa`). Its `ClusterRole` explicitly limits verbs to `get`, `list`, and `watch`. It cannot execute commands (`exec`), delete resources, or escalate privileges.
2.  **Untrusted Data Sanitization:** Logs, Kubernetes resources, application responses, traces, and other observability data should be treated as untrusted input[cite: 1]. The agent employs a regex-based sanitization pipeline (`sanitize_untrusted_data`) that strips known prompt injection vectors (e.g., `ignore previous instructions`, `rm -rf`) before the data reaches the LLM context window.

## Reproducible Setup

### Prerequisites
*   Linux / WSL2 Environment
*   `kind` (Kubernetes in Docker)
*   `kubectl` & `helm`
*   `python3` (with `google-genai` and `kubernetes` packages)

### 1. Cluster Provisioning
Spin up the local `kind` cluster and deploy the Metrics Server:
```bash
./scripts/01-setup-cluster.sh


==================================================
ROOT CAUSE ANALYSIS REPORT
==================================================

## Root Cause
1. **Unbounded Memory Allocation in `payment-processor`**: The `payment-processor-dfdbf8cb-9pmgt` application continuously allocates memory without releasing it, leading to container termination (OOMKilled / memory limit violation) and subsequent container restarts.
2. **Container Application Exit in `user-auth-service`**: The `auth-web` container in `user-auth-service-6cc4fb5cd5-hvd8c` (using the `busybox` image) exits immediately upon execution (likely due to a missing long-running process command or container entrypoint failure), causing Kubernetes to enter a crash restart back-off loop.

## Observed Facts
* Pod `payment-processor-dfdbf8cb-9pmgt` has restarted 1 time and is currently in `Running` state.
* The previous and current execution logs for `payment-processor-dfdbf8cb-9pmgt` show the process repeatedly logging `Allocating memory...` until it abruptly terminates without a clean shutdown message.
* Events show `user-auth-service-6cc4fb5cd5-hvd8c` created container `auth-web` using image `busybox`, started it, and immediately generated a warning: `Back-off restarting failed container auth-web`.
* Pod `checkout-api-fbf6f8977-4rpmw` is healthy and `Running` with 0 restarts.
* The host node `rca-cluster-control-plane` is reported as `NodeReady` with `NodeHasSufficientMemory`, `NodeHasNoDiskPressure`, and `NodeHasSufficientPID`.

## Hypotheses Evaluated
* **Hypothesis 1: Node Resource Exhaustion (Memory/Disk/PID Pressure)**:
  * *Result*: **Ruled Out**. Control plane events explicitly confirm the node has sufficient memory, no disk pressure, sufficient PIDs, and is in state `NodeReady`.
* **Hypothesis 2: `payment-processor` killed due to Out Of Memory (OOM) / Exceeding Container Limits**:
  * *Result*: **Confirmed**. Application logs for both the current and previous container instances end abruptly after consecutive memory allocation steps (`Allocating memory...`), characteristic of an OOM kill.
* **Hypothesis 3: `user-auth-service` failure due to container command misconfiguration**:
  * *Result*: **Confirmed**. A default `busybox` image without a persistent background command exits immediately after starting, triggering `Back-off restarting failed container`.

## Supporting Evidence
* **Crash Logs (`payment-processor-dfdbf8cb-9pmgt`)**: Identical current and previous logs showing repeated `Allocating memory...` prior to abrupt termination.
* **Pod State (`payment-processor-dfdbf8cb-9pmgt`)**: `"restarts": 1`.
* **Cluster Events (`user-auth-service-6cc4fb5cd5-hvd8c`)**: `Started container auth-web` followed immediately by `Warning Back-off restarting failed container auth-web in pod user-auth-service-6cc4fb5cd5-hvd8c`.

## Confidence Level
High (90%)

## Uncertainty
* Exact exit status/code (e.g., exit code 137 for OOMKilled) is not explicitly present in the provided JSON status payloads.
* The `user-auth-service` pod is listed in events but absent from the `Pods` list array, implying it may have been deleted or skipped in the snapshot array.


==================================================
ROOT CAUSE ANALYSIS REPORT
==================================================

## Root Cause
The container `auth-web` in pod `user-auth-service-6cc4fb5cd5-hvd8c` failed and entered a restart back-off loop because it was deployed using the generic `busybox` image without a long-running foreground command or application binary. As a result, the main process completed immediately upon startup, causing Kubernetes to treat the container as failed and trigger a restart loop before replacing the pod.

## Observed Facts
1. Pod `user-auth-service-6cc4fb5cd5-hvd8c` pulled image `busybox` and started container `auth-web`.
2. Immediately following startup, Kubernetes emitted a warning event: `Back-off restarting failed container auth-web in pod user-auth-service-6cc4fb5cd5-hvd8c_default`.
3. A replacement pod `user-auth-service-6cc4fb5cd5-dpmbd` was created by ReplicaSet `user-auth-service-6cc4fb5cd5`.
4. Currently, all active pods in the pod list (`checkout-api-fbf6f8977-4rpmw` and `user-auth-service-6cc4fb5cd5-dpmbd`) are in the `Running` state with 0 restarts.
5. No crash logs were provided in the telemetry data.

## Hypotheses Evaluated
1. **Missing Long-Running Process in Container (Correct)**:
   - *Evaluation*: Highly likely. Using `busybox` for a service named `auth-web` without specifying a continuous daemon command (e.g., web server binary or long-running script) causes the primary PID 1 process to exit immediately with status 0, leading Kubernetes to continually restart or recreate the container/pod.
2. **Node or Infrastructure Failure**:
   - *Evaluation*: Disproved. The node `rca-cluster-control-plane` reported `NodeReady` and successfully scheduled and ran pods.
3. **Image Pull Failure**:
   - *Evaluation*: Disproved. Image pull succeeded within 1.5–5.5 seconds across all reported pull events.

## Supporting Evidence
- Cluster events explicitly show `Started container auth-web` followed shortly by `Back-off restarting failed container auth-web` for pod `user-auth-service-6cc4fb5cd5-hvd8c`.
- Image metadata shows `busybox` (size ~2.2 MB) being used for `auth-web`, which lacks a default continuous service process unless explicit `command`/`args` are supplied in the spec.

## Confidence Level
High (90%)

## Uncertainty
- Pod specifications (`command`, `args`, probes) and application stdout/stderr logs were not provided in the cluster dump, preventing direct verification of the exact command executed inside `auth-web`.


==================================================
ROOT CAUSE ANALYSIS REPORT
==================================================

## Root Cause
The `auth-web` container in the `user-auth-service` deployment uses a standard utility image (`busybox`) without a long-running command specified in its configuration. When started, the default entrypoint exits immediately, causing Kubernetes to register a container failure and repeatedly trigger back-off restarts (`CrashLoopBackOff`).

---

## Observed Facts
1. **Pod Scheduling and Image Pulls**: Pods `user-auth-service-6cc4fb5cd5-dpmbd` and `user-auth-service-6cc4fb5cd5-hvd8c` were successfully scheduled to `rca-cluster-control-plane` and pulled the `busybox` image.
2. **Container Lifecycle**: The `auth-web` container in both pods was created and started, but failed immediately afterwards.
3. **Event Warnings**: Kubernetes generated `Back-off restarting failed container auth-web` warning events for both `user-auth-service` pods.
4. **Current Cluster Pod State**: Neither `user-auth-service` pod is currently listed as `Running` in the pod status list (only `checkout-api` pods are running).

---

## Hypotheses Evaluated
1. **Missing or Non-Blocking Command in `busybox` Image (Confirmed)**: Base `busybox` images default to a shell (`/bin/sh`) which terminates immediately when no interactive terminal or continuous command (e.g., `sleep infinity` or a web server binary) is supplied. This leads to immediate exit upon start.
2. **Image Pull Failure (Rejected)**: Events explicitly confirm `Successfully pulled image "busybox"` within 1.3–5.5 seconds.
3. **Node Resource or Scheduling Constraints (Rejected)**: Pods were successfully assigned and created on `rca-cluster-control-plane`.

---

## Supporting Evidence
* **Event Sequence**: `Created container auth-web` $\rightarrow$ `Started container auth-web` $\rightarrow$ `Back-off restarting failed container auth-web`.
* **Image Context**: The container `auth-web` is running `busybox`, which is missing an active service executable or long-running script required to keep a web service container alive.

---

## Confidence Level
High (90%)

---

## Uncertainty
The specific Pod specification (e.g., `command` / `args` fields) and container exit codes are not explicitly provided in the input, but the behavior pattern is classic for default utility containers exiting immediately upon invocation.
