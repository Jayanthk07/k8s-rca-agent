import os, json, re, datetime
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
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.core_v1 = client.CoreV1Api()
        self.apps_v1 = client.AppsV1Api()

    def get_pods(self, namespace=""):
        # Upgraded to support all namespaces if namespace is empty
        if namespace == "":
            pods = self.core_v1.list_pod_for_all_namespaces()
        else:
            pods = self.core_v1.list_namespaced_pod(namespace)
        
        # Added 'namespace' to the return dict so log fetching knows where to look
        return [{"name": p.metadata.name, "namespace": p.metadata.namespace, "status": p.status.phase, "restarts": sum([c.restart_count for c in p.status.container_statuses or []])} for p in pods.items]

    def get_pod_logs(self, pod_name, namespace="default", previous=False):
        try:
            logs = self.core_v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=50, previous=previous)
            return sanitize_untrusted_data(logs)
        except Exception as e:
            return f"Log retrieval error: {str(e)}"

    def get_events(self, namespace=""):
        if namespace == "":
            events = self.core_v1.list_event_for_all_namespaces()
        else:
            events = self.core_v1.list_namespaced_event(namespace)
        parsed = [f"[{e.type}] {e.involved_object.name}: {e.message}" for e in events.items[-20:]]
        return sanitize_untrusted_data("\n".join(parsed))

    def get_services(self, namespace=""):
        if namespace == "":
            svcs = self.core_v1.list_service_for_all_namespaces()
        else:
            svcs = self.core_v1.list_namespaced_service(namespace)
        return [{"name": s.metadata.name, "type": s.spec.type, "cluster_ip": s.spec.cluster_ip} for s in svcs.items]

    def get_endpoints(self, namespace=""):
        if namespace == "":
            eps = self.core_v1.list_endpoints_for_all_namespaces()
        else:
            eps = self.core_v1.list_namespaced_endpoints(namespace)
        results = []
        for ep in eps.items:
            subsets = ep.subsets if ep.subsets else []
            addresses = sum([len(s.addresses) if s.addresses else 0 for s in subsets])
            results.append({"name": ep.metadata.name, "active_endpoints": addresses})
        return results

def main():
    # Tries to get the key from Kubernetes, otherwise falls back to a placeholder
    api_key = os.environ.get("GEMINI_API_KEY", "your-api-key")

    if not api_key or api_key == "your-api-key":
        print("ERROR: Please replace 'your-api-key' with your actual Gemini API key in single_agent.py")
        return
        
    print("==> Initializing K8s Client...")
    k8s = ReadOnlyK8sClient()
    
    print("==> Gathering Cluster Evidence (Pods, Services, Endpoints, Events, Logs)...")
    
    # Wrapped in try/except to prevent stack-traces if cluster is unreachable
    try:
        pods = k8s.get_pods()
        events = k8s.get_events()
        services = k8s.get_services()
        endpoints = k8s.get_endpoints()

        logs_data = {}
        for p in pods:
            # Dynamically pass the pod's specific namespace so it works across the whole cluster
            ns = p.get("namespace", "default")
            logs_data[p["name"]] = {
                "current": k8s.get_pod_logs(p["name"], namespace=ns),
                "previous": k8s.get_pod_logs(p["name"], namespace=ns, previous=True)
            }
    except Exception as e:
        print(f"[FATAL] Kubernetes API Error: {e}")
        return

    sys_inst = (
        "You are an expert Kubernetes Root-Cause Analysis (RCA) AI.\n"
        "Investigate the provided cluster data. Distinguish between symptoms and the root cause.\n"
        "Format your output strictly using the following Markdown sections:\n"
        "## Root Cause\n## Timeline of Events\n## Observed Facts\n## Hypotheses Evaluated\n## Supporting Evidence\n## Confidence Level\n## Uncertainty"
    )
    
    prompt = (
        f"Cluster State:\nPods: {json.dumps(pods, indent=2)}\n"
        f"Services: {json.dumps(services, indent=2)}\n"
        f"Endpoints: {json.dumps(endpoints, indent=2)}\n\n"
        f"Events:\n{events}\n\nCrash Logs:\n{json.dumps(logs_data, indent=2)}"
    )

    print("==> Analyzing evidence with Gemini API...")
    
    # Wrapped in try/except to handle API drops gracefully
    try:
        llm_client = genai.Client(api_key=api_key)
        response = llm_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=sys_inst, temperature=0.2)
        )
        
        report = response.text
        os.makedirs("reports", exist_ok=True)
        
        # Switched to timestamped reports so you don't lose history
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"reports/rca_{timestamp}.md"
        
        with open(filename, "w") as f:
            f.write(report)
        
        print("\n" + "="*50)
        print("ROOT CAUSE ANALYSIS REPORT")
        print("="*50 + "\n")
        print(report)
        print(f"\n[SUCCESS] Report saved to {filename}")
        
    except Exception as e:
        print(f"[FATAL] LLM API Error: {e}")

if __name__ == "__main__":
    main()