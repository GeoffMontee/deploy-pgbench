terraform {
  required_version = ">= 1.4.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile == "" ? null : var.aws_profile
}

variable "name" {
  type = string
}

variable "owner" {
  type    = string
  default = ""
}

variable "aws_region" {
  type = string
}

variable "aws_profile" {
  type    = string
  default = ""
}

variable "aws_ami_id" {
  type    = string
  default = ""
}

variable "aws_key_name" {
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
  ami_id         = var.aws_ami_id != "" ? var.aws_ami_id : data.aws_ami.ubuntu[0].id
  key_name       = var.aws_key_name != "" ? var.aws_key_name : aws_key_pair.pgbench[0].key_name
  vpc_id         = local.create_network ? aws_vpc.pgbench[0].id : var.vpc_id
  subnet_id      = local.create_network ? aws_subnet.pgbench[0].id : var.subnet_id
  owner_tags     = var.owner == "" ? {} : { Owner = var.owner }
}

data "aws_ami" "ubuntu" {
  count       = var.aws_ami_id == "" ? 1 : 0
  most_recent = true
  owners      = ["099720109477"]

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_key_pair" "pgbench" {
  count      = var.aws_key_name == "" ? 1 : 0
  key_name   = "${var.name}-pgbench"
  public_key = file(var.ssh_public_key_path)
  tags       = merge(local.owner_tags, { Name = "${var.name}-pgbench" })
}

resource "aws_vpc" "pgbench" {
  count                = local.create_network ? 1 : 0
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench"
  })
}

resource "aws_internet_gateway" "pgbench" {
  count  = local.create_network ? 1 : 0
  vpc_id = aws_vpc.pgbench[0].id

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench"
  })
}

resource "aws_subnet" "pgbench" {
  count                   = local.create_network ? 1 : 0
  vpc_id                  = aws_vpc.pgbench[0].id
  cidr_block              = var.subnet_cidr
  map_public_ip_on_launch = var.associate_public_ip

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench"
  })
}

resource "aws_route_table" "pgbench" {
  count  = local.create_network ? 1 : 0
  vpc_id = aws_vpc.pgbench[0].id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.pgbench[0].id
  }

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench"
  })
}

resource "aws_route_table_association" "pgbench" {
  count          = local.create_network ? 1 : 0
  subnet_id      = aws_subnet.pgbench[0].id
  route_table_id = aws_route_table.pgbench[0].id
}

resource "aws_security_group" "pgbench" {
  name        = "${var.name}-pgbench"
  description = "pgbench loader nodes"
  vpc_id      = local.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_cidr]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench"
  })
}

resource "aws_instance" "pgbench" {
  count                       = var.node_count
  ami                         = local.ami_id
  instance_type               = var.instance_type
  subnet_id                   = local.subnet_id
  vpc_security_group_ids      = [aws_security_group.pgbench.id]
  associate_public_ip_address = var.associate_public_ip
  key_name                    = local.key_name

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
  }

  tags = merge(local.owner_tags, {
    Name = "${var.name}-pgbench-${count.index}"
  })
}

output "public_ips" {
  value = aws_instance.pgbench[*].public_ip
}

output "private_ips" {
  value = aws_instance.pgbench[*].private_ip
}

output "ansible_hosts" {
  value = var.associate_public_ip ? aws_instance.pgbench[*].public_ip : aws_instance.pgbench[*].private_ip
}

output "vpc_id" {
  value = local.vpc_id
}

output "subnet_id" {
  value = local.subnet_id
}

output "security_group_id" {
  value = aws_security_group.pgbench.id
}
