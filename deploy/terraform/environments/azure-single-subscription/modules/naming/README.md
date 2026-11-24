# naming (single-subscription deployment)

Deterministic names shared by every component root: a suffix
`substr(sha1("<subscription_id>/<name_prefix>"), 0, 5)` for globally unique names, base tags and
the resource group of each component/party. Roots never need each other's state for naming.
