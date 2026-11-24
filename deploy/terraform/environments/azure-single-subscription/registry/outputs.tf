output "id" {
  description = "Registry resource ID (AcrPull scope for the clean-room cluster)."
  value       = azurerm_container_registry.this.id
}

output "login_server" {
  description = "Registry login server (clean-room image repository)."
  value       = azurerm_container_registry.this.login_server
}
