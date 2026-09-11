import os, json, re
from kubernetes import client, config
from google import genai
from google.genai import types

def sanitize_untrusted_data(raw_text: str, max_chars: int = 4000) -> str:
    if not raw_text: return ""
    sanitized = str(raw_text)[:max_chars]
    for p in [r"ignore\s+all\s+previous\s+instructions", r"system\s*override", r"exec\(", r"rm\s+-rf"]:
        sanitized = re.sub(p, "[FILTERED_UNTRUSTED_INSTRUCTION]", sanitized, flags=re.IGNORECASE)
    return sanitized

class ReadOnlyK8sClient:
    def __init__(self):
        config.load_kube_config()
        self.core_v1 = client.CoreV1Api()

    def get_pods(self, namespace="default"):
        pods = self.core_v1.list_namespaced_pod(namespace)
        return [{"name": p.metadata.name, "status": p.status.phase, "restarts": sum([c.restart_count for c in p.status.container_statuses or []])} for p in pods.items]

    def get_pod_logs(self, pod_name, namespace="default", previous=False):
        try:
            logs = self.core_v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=50, previous=previous)
            return sanitize_untrusted_data(logs)
        except Exception as e:
            return f"Log retrieval error: {str(e)}"

    def get_events(self, namespace="default"):
        events = self.core_v1.list_namespaced_event(namespace)
        parsed = [f"[{e.type}] {e.involved_object.name}: {e.message}" for e in events.items[-20:]]
        return sanitize_untrusted_data("\n".join(parsed))

def main():
    api_key = "gemini-api-key"
    if not api_key:
        print("ERROR: Please set your GEMINI_API_KEY environment variable.")
        return
        
    print("==> Initializing K8s Client...")
    k8s = ReadOnlyK8sClient()
    
    print("==> Gathering Cluster Evidence (Pods, Events, Logs)...")
    pods = k8s.get_pods()
    events = k8s.get_events()
    
    logs_data = {}
    for p in pods:
        if p["restarts"] > 0 or p["status"] != "Running":
            logs_data[p["name"]] = {
                "current": k8s.get_pod_logs(p["name"]),
                "previous": k8s.get_pod_logs(p["name"], previous=True)
            }

    sys_inst = (
        "You are an expert Kubernetes Root-Cause Analysis (RCA) AI.\n"
        "Investigate the provided cluster data. Distinguish between symptoms and the root cause.\n"
        "Format your output strictly using the following Markdown sections:\n"
        "## Root Cause\n## Observed Facts\n## Hypotheses Evaluated\n## Supporting Evidence\n## Confidence Level\n## Uncertainty"
    )
    prompt = f"Cluster State:\nPods: {json.dumps(pods, indent=2)}\n\nEvents:\n{events}\n\nCrash Logs:\n{json.dumps(logs_data, indent=2)}"

    print("==> Analyzing evidence with Gemini API...")
    llm_client = genai.Client(api_key=api_key)
    response = llm_client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt,
        config=types.GenerateContentConfig(system_instruction=sys_inst, temperature=0.2)
    )
    
    report = response.text
    os.makedirs("reports", exist_ok=True)
    with open("reports/latest_rca.md", "w") as f:
        f.write(report)
    
    print("\n" + "="*50)
    print("ROOT CAUSE ANALYSIS REPORT")
    print("="*50 + "\n")
    print(report)

if __name__ == "__main__":
    main()
