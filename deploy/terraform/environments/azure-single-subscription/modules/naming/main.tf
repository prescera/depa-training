# Deterministic names shared by every component root of the single-subscription deployment, so
# the roots never need each other's state just to agree on names. The suffix keeps globally
# scoped names (ledgers, storage accounts, registry, function apps) unique per subscription and
# prefix.
locals {
  suffix = substr(sha1("${var.subscription_id}/${var.name_prefix}"), 0, 5)
}
