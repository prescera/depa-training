locals {
  vn2_images = {
    for k, name in {
      crisockettotcpadapterImage = "cri-socket-adapter"
      spdycompatImage            = "spdy-compat"
      proxycriImage              = "proxy-cri"
      mountImage                 = "mount-host"
      initImage                  = "init-config"
      kubeletImage               = "kubelet"
      virtualcsiImage            = "virtual-csi"
      admissioncontrollerImage   = "admission-controller"
    } : k => "mcr.microsoft.com/aci/virtual-node-2-${name}:${var.vn2_image_tag}"
  }
}

# Virtual nodes v2: schedules pods onto (confidential) Azure Container Instances. A pod carrying
# the microsoft.containerinstance.virtualnode.ccepolicy annotation becomes a Confidential
# container group whose SEV-SNP HOST_DATA is sha256(policy).
resource "helm_release" "virtual_node" {
  name       = "virtualnode"
  repository = "https://microsoft.github.io/virtualnodesOnAzureContainerInstances"
  chart      = "virtualnode"
  version    = var.vn2_chart_version
  # As in DEPA-inferencing: the release lives in "default" and the chart creates its own
  # namespace from the `namespace` value; letting Helm create it as well fails with
  # "namespaces ... already exists".
  atomic  = true
  timeout = 900

  values = [yamlencode({
    namespace                       = var.vn2_namespace
    replicaCount                    = 1
    admissionControllerReplicaCount = 1
    aciSubnetName                   = var.aci_subnet_name
    aciResourceGroupName            = var.aci_resource_group_name
    zones                           = join(";", var.zones)
    sandboxProviderType             = "OnDemand"
    customTags                      = join(";", [for k, v in var.custom_tags : "${k}=${v}"])
    nodeSelector                    = { "kubernetes.io/os" = "linux" }
    acrTrustedAccess = {
      enabled             = var.acr_pull_identity != null
      identityResourceId  = var.acr_pull_identity == null ? "" : var.acr_pull_identity.id
      identityPrincipalId = var.acr_pull_identity == null ? "" : var.acr_pull_identity.principal_id
    }
    kubeProxy = {
      enabled          = true
      reservedMemoryGB = 0.1
      reservedCPUCores = 0.1
    }
    images = merge(local.vn2_images, {
      pullPolicy              = "IfNotPresent"
      pullSecrets             = []
      mountConfidImage        = "mcr.microsoft.com/aci/virtual-node-2-mount-host:${var.vn2_mount_confidential_image_tag}"
      noddriverregistrarImage = "mcr.microsoft.com/oss/kubernetes-csi/csi-node-driver-registrar:v2.8.0"
    })
  })]
}

resource "kubernetes_namespace_v1" "ccr" {
  metadata {
    name = var.ccr_namespace
    labels = {
      "app.kubernetes.io/part-of" = "depa-training"
      # The CCR agent starts as root and drops to uid 10001 for the trainer, so "restricted"
      # (runAsNonRoot) cannot be enforced; "baseline" still forbids privileged pods and host
      # namespaces/paths. The CCE policy is the binding control.
      "pod-security.kubernetes.io/enforce" = "baseline"
      "pod-security.kubernetes.io/warn"    = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
  }
}

resource "kubernetes_resource_quota_v1" "ccr" {
  metadata {
    name      = "ccr-quota"
    namespace = kubernetes_namespace_v1.ccr.metadata[0].name
  }

  spec {
    hard = {
      "count/jobs.batch" = tostring(var.max_concurrent_jobs)
      pods               = tostring(var.max_concurrent_jobs)
      "limits.cpu"       = var.max_namespace_cpu
      "limits.memory"    = var.max_namespace_memory
      secrets            = "0"
      configmaps         = "1" # kube-root-ca.crt, created by Kubernetes in every namespace
      services           = "0"
    }
  }
}

# Least privilege for submitters: manage Jobs, observe their pods. No pods/exec, no secrets,
# no direct pod creation (a Job's pod template is what the CCE policy measures).
resource "kubernetes_role_v1" "job_submitter" {
  metadata {
    name      = "ccr-job-submitter"
    namespace = kubernetes_namespace_v1.ccr.metadata[0].name
  }

  rule {
    api_groups = ["batch"]
    resources  = ["jobs"]
    verbs      = ["create", "get", "list", "watch", "delete"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "events"]
    verbs      = ["get", "list", "watch"]
  }

  rule {
    api_groups = [""]
    resources  = ["pods/log"]
    verbs      = ["get"]
  }
}

resource "kubernetes_role_binding_v1" "job_submitter" {
  count = length(var.job_submitters) > 0 ? 1 : 0

  metadata {
    name      = "ccr-job-submitter"
    namespace = kubernetes_namespace_v1.ccr.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.job_submitter.metadata[0].name
  }

  dynamic "subject" {
    for_each = var.job_submitters
    content {
      api_group = "rbac.authorization.k8s.io"
      kind      = subject.value.kind
      name      = subject.value.object_id
    }
  }
}
