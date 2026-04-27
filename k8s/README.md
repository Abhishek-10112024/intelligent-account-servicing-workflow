# Kubernetes Deployment Guide for IASW

## Prerequisites

- Kubernetes cluster (v1.24+) — EKS, GKE, AKS, or local minikube
- `kubectl` CLI installed and configured
- Docker image registry access (e.g., Docker Hub, ECR, GCR)
- Helm (optional, for advanced deployments)

## Quick Start

### 1. Build and Push Docker Image

```bash
# Build image
docker build -t your-registry/iasw:latest .

# Push to registry
docker push your-registry/iasw:latest
```

### 2. Update Image in backend.yaml

Edit `k8s/backend.yaml` and replace:
```yaml
image: iasw/backend:latest
```
with your actual image:
```yaml
image: your-registry/iasw:latest
```

### 3. Update Secrets

Edit `k8s/secrets.yaml` and replace:
```yaml
GEMINI_API_KEY: "YOUR_GEMINI_API_KEY_HERE"
```
with your actual Gemini API key.

### 4. Deploy to Cluster

```bash
# Create namespace and deploy all resources
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secrets.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
kubectl apply -f k8s/backend.yaml
kubectl apply -f k8s/ingress.yaml

# Or deploy all at once
kubectl apply -f k8s/

# Verify deployments
kubectl get pods -n iasw
kubectl get services -n iasw
```

### 5. Check Status

```bash
# Wait for all pods to be ready
kubectl wait --for=condition=ready pod -l app=iasw-backend -n iasw --timeout=300s

# Get logs from backend
kubectl logs -f deployment/iasw-backend -n iasw

# Port-forward to access locally
kubectl port-forward svc/iasw-backend-service 8000:80 -n iasw
# Then visit http://localhost:8000
```

## Architecture

```
Internet
    ↓
Ingress (iasw-ingress)
    ↓
Service (iasw-backend-service) → Pods (3 replicas, HPA: 3-10)
    ↓
  ├─→ PostgreSQL (iasw_db)
  └─→ Redis (cache)
```

## Configuration

### Environment Variables

All environment variables are managed through:
- **ConfigMap** (`k8s/configmap.yaml`): Non-sensitive config
- **Secrets** (`k8s/secrets.yaml`): Sensitive data (API keys, passwords)

To update config after deployment:
```bash
kubectl edit configmap iasw-config -n iasw
# This will restart pods automatically
```

### Resource Limits

Adjust in `k8s/backend.yaml`:
```yaml
resources:
  requests:
    cpu: 200m      # Minimum guaranteed CPU
    memory: 512Mi   # Minimum guaranteed memory
  limits:
    cpu: 500m      # Maximum CPU
    memory: 1Gi    # Maximum memory
```

### Scaling

Manual scaling:
```bash
kubectl scale deployment iasw-backend --replicas=5 -n iasw
```

Automatic scaling is managed by HorizontalPodAutoscaler (HPA):
- Minimum replicas: 3
- Maximum replicas: 10
- Triggers: CPU 70%, Memory 80%

Check HPA status:
```bash
kubectl get hpa -n iasw
```

## Database Persistence (Production)

The current setup uses `emptyDir` volumes (data lost on pod restart).

For production, create PersistentVolumeClaims:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
  namespace: iasw
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: standard  # Adjust for your cluster
  resources:
    requests:
      storage: 10Gi

---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: redis-pvc
  namespace: iasw
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: standard
  resources:
    requests:
      storage: 5Gi
```

Then update postgres.yaml and redis.yaml to use these PVCs.

## Monitoring & Logging

### View Logs

```bash
# Current pod logs
kubectl logs -f deployment/iasw-backend -n iasw

# Logs from specific pod
kubectl logs -f pod/iasw-backend-xxxxx -n iasw --all-containers

# Previous pod logs (if crashed)
kubectl logs -f pod/iasw-backend-xxxxx -n iasw --previous
```

### Monitor Metrics

Install Prometheus + Grafana for production monitoring:
```bash
helm install prometheus prometheus-community/kube-prometheus-stack -n monitoring
```

### Health Checks

The backend exposes health endpoint:
```bash
kubectl exec -it pod/iasw-backend-xxxxx -n iasw -- curl localhost:8000/health
```

## Updating the Deployment

### Update Image

```bash
# Push new image
docker build -t your-registry/iasw:v2.0.0 .
docker push your-registry/iasw:v2.0.0

# Update deployment
kubectl set image deployment/iasw-backend iasw=your-registry/iasw:v2.0.0 -n iasw
```

### Update Config

```bash
# Edit and reapply
kubectl edit configmap iasw-config -n iasw
```

### Rollback

```bash
# View rollout history
kubectl rollout history deployment/iasw-backend -n iasw

# Rollback to previous version
kubectl rollout undo deployment/iasw-backend -n iasw
```

## Cleanup

```bash
# Delete entire namespace (all resources)
kubectl delete namespace iasw

# Or delete specific resources
kubectl delete -f k8s/
```

## Production Checklist

- [ ] Update image registry and credentials
- [ ] Set strong passwords in secrets.yaml
- [ ] Enable TLS/HTTPS in ingress.yaml
- [ ] Configure PersistentVolumes for data durability
- [ ] Set up monitoring (Prometheus + Grafana)
- [ ] Enable pod disruption budgets
- [ ] Configure autoscaling policies
- [ ] Set up backup strategy for database
- [ ] Use managed services (RDS, ElastiCache) instead of pods
- [ ] Implement network policies
- [ ] Use private image registry with authentication
- [ ] Set up CI/CD pipeline (GitOps with ArgoCD)

## Troubleshooting

### Pods not starting

```bash
# Check pod status
kubectl describe pod iasw-backend-xxxxx -n iasw

# Check events
kubectl get events -n iasw --sort-by='.lastTimestamp'
```

### Database connection issues

```bash
# Test database connectivity
kubectl run -it --rm debug --image=postgres:15 --restart=Never -n iasw -- \
  psql -h postgres-service -U postgres -d iasw_db -c "SELECT 1"
```

### Redis connectivity

```bash
# Test Redis
kubectl run -it --rm debug --image=redis:7 --restart=Never -n iasw -- \
  redis-cli -h redis-service ping
```

### Out of memory errors

```bash
# Increase memory limits in backend.yaml
# And check pod memory usage
kubectl top pod -n iasw
```

## Support

For Kubernetes-specific issues, consult:
- [Kubernetes Documentation](https://kubernetes.io/docs/)
- [Your Cloud Provider's K8s Guide](https://aws.amazon.com/eks/ | https://cloud.google.com/kubernetes-engine | https://azure.microsoft.com/en-us/services/kubernetes-service/)
