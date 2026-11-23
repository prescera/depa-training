# Trust in the DC KMS OIDC issuer (spec §10, AWS).

resource "aws_iam_openid_connect_provider" "kms" {
  count = var.create_oidc_provider ? 1 : 0

  url             = var.oidc_issuer_url
  client_id_list  = [local.sts_audience]
  thumbprint_list = length(var.oidc_thumbprints) > 0 ? var.oidc_thumbprints : null
  tags            = local.tags
}

data "aws_iam_policy_document" "trust" {
  for_each = var.grants

  statement {
    sid     = "KmsIssuedTokenForSubject"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host_path}:aud"
      values   = [local.sts_audience]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host_path}:sub"
      values   = [each.key]
    }
  }
}

resource "aws_iam_role" "grant" {
  for_each = var.grants

  name                 = local.role_names[each.key]
  description          = "DEPA CCR ${each.value.access} access for OIDC subject ${each.key}"
  assume_role_policy   = data.aws_iam_policy_document.trust[each.key].json
  max_session_duration = var.max_session_duration
  permissions_boundary = var.permissions_boundary_arn
  tags                 = merge(local.tags, { depa_subject = each.key, depa_access = each.value.access })
}

data "aws_iam_policy_document" "access" {
  for_each = var.grants

  # Read: fetch manifest.json/manifest.jws and the listed objects only. No
  # s3:ListBucket: readers never enumerate (spec §7.3).
  dynamic "statement" {
    for_each = each.value.access == "read" ? [1] : []
    content {
      sid       = "ReadDatasetObjects"
      effect    = "Allow"
      actions   = ["s3:GetObject", "s3:GetObjectVersion"]
      resources = ["${local.bucket_arn}/${each.value.prefix}*"]
    }
  }

  # Write: create objects (single-part and multipart). No read/delete; bucket
  # versioning keeps any overwritten version recoverable by the owner.
  dynamic "statement" {
    for_each = each.value.access == "write" ? [1] : []
    content {
      sid       = "CreateOutputObjects"
      effect    = "Allow"
      actions   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
      resources = ["${local.bucket_arn}/${each.value.prefix}*"]
    }
  }

  dynamic "statement" {
    for_each = var.kms_key_arn == null ? [] : [1]
    content {
      sid       = "UseBucketKmsKeyViaS3"
      effect    = "Allow"
      actions   = each.value.access == "read" ? ["kms:Decrypt"] : ["kms:Decrypt", "kms:GenerateDataKey"]
      resources = [var.kms_key_arn]

      condition {
        test     = "StringEquals"
        variable = "kms:ViaService"
        values   = ["s3.${data.aws_region.current.region}.amazonaws.com"]
      }

      condition {
        test     = "StringLike"
        variable = "kms:EncryptionContext:aws:s3:arn"
        values   = [local.bucket_arn, "${local.bucket_arn}/${each.value.prefix}*"]
      }
    }
  }
}

resource "aws_iam_role_policy" "access" {
  for_each = var.grants

  name   = "depa-${each.value.access}"
  role   = aws_iam_role.grant[each.key].id
  policy = data.aws_iam_policy_document.access[each.key].json
}
