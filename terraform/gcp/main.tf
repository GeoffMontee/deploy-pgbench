terraform {
  required_version = ">= 1.4.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project     = var.gcp_project
  region      = var.gcp_region
  zone        = var.gcp_zone
  credentials = var.gcp_service_account_file == "" ? null : file(var.gcp_service_account_file)
}

variable "name" {
  type = string
}

variable "owner" {
  type    = string
  default = ""
}

variable "gcp_project" {
  type = string
}

variable "gcp_region" {
  type = string
}

variable "gcp_zone" {
  type = string
}

variable "gcp_image" {
  type = string
}

variable "gcp_service_account_file" {
  type    = string
  default = ""
}

variable "associate_public_ip" {
  type = bool
}

variable "instance_type" {
  type = string
}

variable "node_count" {
  type = number
}

variable "ssh_user" {
  type = string
}

variable "ssh_public_key_path" {
  type = string
}

variable "ssh_cidr" {
  type = string
}

variable "vpc_id" {
  type    = string
  default = ""
}

variable "subnet_id" {
  type    = string
  default = ""
}

variable "vpc_cidr" {
  type = string
}

variable "subnet_cidr" {
  type = string
}

locals {
  create_network = var.vpc_id == "" && var.subnet_id == ""
  network_id     = local.create_network ? google_compute_network.pgbench[0].self_link : var.vpc_id
  subnetwork_id  = local.create_network ? google_compute_subnetwork.pgbench[0].self_link : var.subnet_id
  owner_labels   = var.owner == "" ? {} : { owner = substr(replace(lower(var.owner), "/[^a-z0-9_-]/", "-"), 0, 63) }
  owner_metadata = var.owner == "" ? {} : { owner = var.owner }
}

resource "google_compute_network" "pgbench" {
  count                   = local.create_network ? 1 : 0
  name                    = "${var.name}-pgbench"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "pgbench" {
  count         = local.create_network ? 1 : 0
  name          = "${var.name}-pgbench"
  ip_cidr_range = var.subnet_cidr
  region        = var.gcp_region
  network       = google_compute_network.pgbench[0].id
}

resource "google_compute_firewall" "pgbench_ssh" {
  name    = "${var.name}-pgbench-ssh"
  network = local.network_id

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [var.ssh_cidr]
  target_tags   = ["${var.name}-pgbench"]
}

resource "google_compute_instance" "pgbench" {
  count        = var.node_count
  name         = "${var.name}-pgbench-${count.index}"
  machine_type = var.instance_type
  zone         = var.gcp_zone
  tags         = ["${var.name}-pgbench"]
  labels       = local.owner_labels

  boot_disk {
    initialize_params {
      image = var.gcp_image
      size  = 50
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = local.subnetwork_id

    dynamic "access_config" {
      for_each = var.associate_public_ip ? [1] : []
      content {}
    }
  }

  metadata = merge(
    {
      ssh-keys = "${var.ssh_user}:${file(var.ssh_public_key_path)}"
    },
    local.owner_metadata
  )
}

output "public_ips" {
  value = [for instance in google_compute_instance.pgbench : try(instance.network_interface[0].access_config[0].nat_ip, null)]
}

output "private_ips" {
  value = [for instance in google_compute_instance.pgbench : instance.network_interface[0].network_ip]
}

output "ansible_hosts" {
  value = var.associate_public_ip ? [for instance in google_compute_instance.pgbench : instance.network_interface[0].access_config[0].nat_ip] : [for instance in google_compute_instance.pgbench : instance.network_interface[0].network_ip]
}

output "network" {
  value = local.network_id
}

output "subnetwork" {
  value = local.subnetwork_id
}
