#!/usr/bin/env bash
set -e

echo "==> Creating kind cluster: rca-cluster..."
kind create cluster --name rca-cluster

echo "==> Deploying Metrics Server for pod utilization tracking..."
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
kubectl patch deployment metrics-server -n kube-system --type='json' \
  -p='[{"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}]'

echo "==> Cluster ready."
