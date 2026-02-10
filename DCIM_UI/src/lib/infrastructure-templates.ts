// Infrastructure template generator based on user intent

interface InfrastructureResult {
  terraform: string
  terragrunt: string
  description: string
}

export function generateInfrastructure(query: string): InfrastructureResult {
  const lowerQuery = query.toLowerCase()

  // AWS VPC
  if (lowerQuery.includes('vpc') || lowerQuery.includes('network')) {
    return generateVPC(query)
  }

  // S3 Bucket
  if (lowerQuery.includes('s3') || lowerQuery.includes('bucket') || lowerQuery.includes('storage')) {
    return generateS3(query)
  }

  // EC2 / Compute
  if (lowerQuery.includes('ec2') || lowerQuery.includes('instance') || lowerQuery.includes('vm') || lowerQuery.includes('server')) {
    return generateEC2(query)
  }

  // Kubernetes / EKS
  if (lowerQuery.includes('kubernetes') || lowerQuery.includes('k8s') || lowerQuery.includes('eks') || lowerQuery.includes('cluster')) {
    return generateKubernetes(query)
  }

  // RDS / Database
  if (lowerQuery.includes('rds') || lowerQuery.includes('database') || lowerQuery.includes('postgres') || lowerQuery.includes('mysql')) {
    return generateRDS(query)
  }

  // Load Balancer
  if (lowerQuery.includes('load balancer') || lowerQuery.includes('alb') || lowerQuery.includes('elb')) {
    return generateLoadBalancer(query)
  }

  // 3-tier application
  if (lowerQuery.includes('3-tier') || lowerQuery.includes('three tier') || lowerQuery.includes('web app')) {
    return generate3TierApp(query)
  }

  // Lambda / Serverless
  if (lowerQuery.includes('lambda') || lowerQuery.includes('serverless') || lowerQuery.includes('function')) {
    return generateLambda(query)
  }

  // Default: basic AWS infrastructure
  return generateBasicInfrastructure(query)
}

function generateVPC(query: string): InfrastructureResult {
  return {
    description: "AWS VPC with public and private subnets",
    terraform: `# AWS VPC Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "CIDR block for VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# Create VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "\${var.environment}-vpc"
    Environment = var.environment
  }
}

# Public Subnets
resource "aws_subnet" "public" {
  count                   = 3
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name        = "\${var.environment}-public-subnet-\${count.index + 1}"
    Environment = var.environment
    Type        = "public"
  }
}

# Private Subnets
resource "aws_subnet" "private" {
  count             = 3
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name        = "\${var.environment}-private-subnet-\${count.index + 1}"
    Environment = var.environment
    Type        = "private"
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name        = "\${var.environment}-igw"
    Environment = var.environment
  }
}

# NAT Gateway
resource "aws_eip" "nat" {
  count  = 3
  domain = "vpc"

  tags = {
    Name        = "\${var.environment}-nat-eip-\${count.index + 1}"
    Environment = var.environment
  }
}

resource "aws_nat_gateway" "main" {
  count         = 3
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name        = "\${var.environment}-nat-\${count.index + 1}"
    Environment = var.environment
  }
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name        = "\${var.environment}-public-rt"
    Environment = var.environment
  }
}

resource "aws_route_table" "private" {
  count  = 3
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = {
    Name        = "\${var.environment}-private-rt-\${count.index + 1}"
    Environment = var.environment
  }
}

# Route Table Associations
resource "aws_route_table_association" "public" {
  count          = 3
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 3
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Data source for availability zones
data "aws_availability_zones" "available" {
  state = "available"
}

# Outputs
output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs"
  value       = aws_subnet.private[*].id
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region  = "us-east-1"
  vpc_cidr    = "10.0.0.0/16"
  environment = "production"
}`
  }
}

function generateS3(query: string): InfrastructureResult {
  return {
    description: "AWS S3 bucket with versioning and encryption",
    terraform: `# AWS S3 Bucket Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "S3 bucket name"
  type        = string
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# S3 Bucket
resource "aws_s3_bucket" "main" {
  bucket = var.bucket_name

  tags = {
    Name        = var.bucket_name
    Environment = var.environment
  }
}

# Enable versioning
resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Enable encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Block public access
resource "aws_s3_bucket_public_access_block" "main" {
  bucket = aws_s3_bucket.main.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Lifecycle policy
resource "aws_s3_bucket_lifecycle_configuration" "main" {
  bucket = aws_s3_bucket.main.id

  rule {
    id     = "archive-old-versions"
    status = "Enabled"

    noncurrent_version_transition {
      noncurrent_days = 30
      storage_class   = "GLACIER"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# Outputs
output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.main.id
}

output "bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.main.arn
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region  = "us-east-1"
  bucket_name = "my-app-bucket-\${get_aws_account_id()}"
  environment = "production"
}`
  }
}

function generateEC2(query: string): InfrastructureResult {
  return {
    description: "AWS EC2 instance with security group",
    terraform: `# AWS EC2 Instance Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# Security Group
resource "aws_security_group" "instance" {
  name        = "\${var.environment}-instance-sg"
  description = "Security group for EC2 instance"

  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "SSH access"
  }

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP access"
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS access"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound traffic"
  }

  tags = {
    Name        = "\${var.environment}-instance-sg"
    Environment = var.environment
  }
}

# Latest Amazon Linux 2 AMI
data "aws_ami" "amazon_linux_2" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["amzn2-ami-hvm-*-x86_64-gp2"]
  }
}

# EC2 Instance
resource "aws_instance" "main" {
  ami                    = data.aws_ami.amazon_linux_2.id
  instance_type          = var.instance_type
  vpc_security_group_ids = [aws_security_group.instance.id]

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name        = "\${var.environment}-instance"
    Environment = var.environment
  }
}

# Outputs
output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.main.id
}

output "public_ip" {
  description = "Public IP address"
  value       = aws_instance.main.public_ip
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region    = "us-east-1"
  instance_type = "t3.medium"
  environment   = "production"
}`
  }
}

function generateKubernetes(query: string): InfrastructureResult {
  return {
    description: "AWS EKS Kubernetes cluster",
    terraform: `# AWS EKS Cluster Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "cluster_name" {
  description = "EKS cluster name"
  type        = string
  default     = "production-eks"
}

variable "kubernetes_version" {
  description = "Kubernetes version"
  type        = string
  default     = "1.28"
}

variable "node_instance_type" {
  description = "EC2 instance type for worker nodes"
  type        = string
  default     = "t3.large"
}

# EKS Cluster
resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids = var.subnet_ids
  }

  depends_on = [
    aws_iam_role_policy_attachment.cluster_policy
  ]
}

# IAM Role for EKS Cluster
resource "aws_iam_role" "cluster" {
  name = "\${var.cluster_name}-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.cluster.name
}

# EKS Node Group
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "\${var.cluster_name}-nodes"
  node_role_arn   = aws_iam_role.node_group.arn
  subnet_ids      = var.subnet_ids

  scaling_config {
    desired_size = 3
    max_size     = 5
    min_size     = 2
  }

  instance_types = [var.node_instance_type]

  depends_on = [
    aws_iam_role_policy_attachment.node_policy,
    aws_iam_role_policy_attachment.cni_policy,
    aws_iam_role_policy_attachment.registry_policy
  ]
}

# IAM Role for Node Group
resource "aws_iam_role" "node_group" {
  name = "\${var.cluster_name}-node-group-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.node_group.name
}

resource "aws_iam_role_policy_attachment" "cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.node_group.name
}

resource "aws_iam_role_policy_attachment" "registry_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.node_group.name
}

# Outputs
output "cluster_endpoint" {
  description = "EKS cluster endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "cluster_name" {
  description = "EKS cluster name"
  value       = aws_eks_cluster.main.name
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region         = "us-east-1"
  cluster_name       = "production-eks"
  kubernetes_version = "1.28"
  node_instance_type = "t3.large"
}`
  }
}

function generateRDS(query: string): InfrastructureResult {
  return {
    description: "AWS RDS PostgreSQL database",
    terraform: `# AWS RDS PostgreSQL Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "db_name" {
  description = "Database name"
  type        = string
  default     = "myapp"
}

variable "db_username" {
  description = "Database master username"
  type        = string
  default     = "admin"
}

variable "db_password" {
  description = "Database master password"
  type        = string
  sensitive   = true
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.medium"
}

# DB Subnet Group
resource "aws_db_subnet_group" "main" {
  name       = "\${var.db_name}-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "\${var.db_name}-subnet-group"
  }
}

# Security Group for RDS
resource "aws_security_group" "rds" {
  name        = "\${var.db_name}-rds-sg"
  description = "Security group for RDS instance"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["10.0.0.0/16"]
    description = "PostgreSQL access from VPC"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "\${var.db_name}-rds-sg"
  }
}

# RDS Instance
resource "aws_db_instance" "main" {
  identifier             = var.db_name
  engine                 = "postgres"
  engine_version         = "15.3"
  instance_class         = var.instance_class
  allocated_storage      = 100
  storage_type           = "gp3"
  storage_encrypted      = true
  db_name                = var.db_name
  username               = var.db_username
  password               = var.db_password
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  multi_az               = true
  backup_retention_period = 7
  skip_final_snapshot    = false
  final_snapshot_identifier = "\${var.db_name}-final-snapshot"

  tags = {
    Name = "\${var.db_name}-rds"
  }
}

# Outputs
output "db_endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.main.endpoint
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.main.db_name
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region     = "us-east-1"
  db_name        = "myapp"
  db_username    = "admin"
  db_password    = "CHANGE_ME_IN_PRODUCTION"  # Use AWS Secrets Manager in production
  instance_class = "db.t3.medium"
}`
  }
}

function generateLoadBalancer(query: string): InfrastructureResult {
  return {
    description: "AWS Application Load Balancer",
    terraform: `# AWS Application Load Balancer Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# Security Group for ALB
resource "aws_security_group" "alb" {
  name        = "\${var.environment}-alb-sg"
  description = "Security group for Application Load Balancer"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP from anywhere"
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS from anywhere"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Allow all outbound"
  }

  tags = {
    Name        = "\${var.environment}-alb-sg"
    Environment = var.environment
  }
}

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "\${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  enable_deletion_protection = true
  enable_http2               = true

  tags = {
    Name        = "\${var.environment}-alb"
    Environment = var.environment
  }
}

# Target Group
resource "aws_lb_target_group" "main" {
  name     = "\${var.environment}-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = var.vpc_id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    interval            = 30
    matcher             = "200"
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 5
    unhealthy_threshold = 2
  }

  tags = {
    Name        = "\${var.environment}-tg"
    Environment = var.environment
  }
}

# HTTP Listener
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}

# Outputs
output "alb_dns_name" {
  description = "ALB DNS name"
  value       = aws_lb.main.dns_name
}

output "target_group_arn" {
  description = "Target group ARN"
  value       = aws_lb_target_group.main.arn
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

# Remote state configuration
remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

# Input variables
inputs = {
  aws_region  = "us-east-1"
  environment = "production"
}`
  }
}

function generate3TierApp(query: string): InfrastructureResult {
  return {
    description: "3-tier web application infrastructure",
    terraform: `# 3-Tier Web Application Infrastructure
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# This is a comprehensive setup that includes:
# - Application Load Balancer (Web tier)
# - Auto Scaling Group with EC2 instances (App tier)
# - RDS Database (Database tier)
# - VPC with public and private subnets

# Security Groups
resource "aws_security_group" "alb" {
  name        = "\${var.environment}-alb-sg"
  description = "ALB security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "app" {
  name        = "\${var.environment}-app-sg"
  description = "Application tier security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8080
    to_port         = 8080
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "Allow traffic from ALB"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "db" {
  name        = "\${var.environment}-db-sg"
  description = "Database tier security group"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
    description     = "Allow traffic from app tier"
  }
}

# Application Load Balancer
resource "aws_lb" "main" {
  name               = "\${var.environment}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids

  tags = {
    Name        = "\${var.environment}-alb"
    Environment = var.environment
  }
}

# Auto Scaling Group
resource "aws_launch_template" "app" {
  name_prefix   = "\${var.environment}-app-"
  image_id      = data.aws_ami.amazon_linux_2.id
  instance_type = "t3.medium"

  vpc_security_group_ids = [aws_security_group.app.id]

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name        = "\${var.environment}-app-instance"
      Environment = var.environment
    }
  }
}

resource "aws_autoscaling_group" "app" {
  name                = "\${var.environment}-asg"
  vpc_zone_identifier = var.private_subnet_ids
  target_group_arns   = [aws_lb_target_group.app.arn]
  health_check_type   = "ELB"
  min_size            = 2
  max_size            = 10
  desired_capacity    = 3

  launch_template {
    id      = aws_launch_template.app.id
    version = "$Latest"
  }
}

# RDS Database
resource "aws_db_instance" "main" {
  identifier             = "\${var.environment}-db"
  engine                 = "postgres"
  engine_version         = "15.3"
  instance_class         = "db.t3.medium"
  allocated_storage      = 100
  storage_encrypted      = true
  db_name                = "appdb"
  username               = "admin"
  password               = var.db_password
  vpc_security_group_ids = [aws_security_group.db.id]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  multi_az               = true
  skip_final_snapshot    = false
}

# Outputs
output "alb_dns_name" {
  value = aws_lb.main.dns_name
}

output "db_endpoint" {
  value = aws_db_instance.main.endpoint
}`,
    terragrunt: `# Terragrunt Configuration for 3-Tier App
terraform {
  source = "./"
}

remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

inputs = {
  aws_region  = "us-east-1"
  environment = "production"
  db_password = "CHANGE_ME_IN_PRODUCTION"  # Use AWS Secrets Manager
}`
  }
}

function generateLambda(query: string): InfrastructureResult {
  return {
    description: "AWS Lambda function with API Gateway",
    terraform: `# AWS Lambda Function Configuration
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "function_name" {
  description = "Lambda function name"
  type        = string
  default     = "my-function"
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda" {
  name = "\${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_policy" {
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
  role       = aws_iam_role.lambda.name
}

# Lambda Function
resource "aws_lambda_function" "main" {
  filename      = "lambda.zip"
  function_name = var.function_name
  role          = aws_iam_role.lambda.arn
  handler       = "index.handler"
  runtime       = "nodejs18.x"
  timeout       = 30
  memory_size   = 512

  environment {
    variables = {
      ENVIRONMENT = "production"
    }
  }

  tags = {
    Name = var.function_name
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/\${var.function_name}"
  retention_in_days = 14
}

# API Gateway
resource "aws_apigatewayv2_api" "main" {
  name          = "\${var.function_name}-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda" {
  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.main.invoke_arn
  integration_method = "POST"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "$default"
  target    = "integrations/\${aws_apigatewayv2_integration.lambda.id}"
}

resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "production"
  auto_deploy = true
}

resource "aws_lambda_permission" "api_gateway" {
  statement_id  = "AllowExecutionFromAPIGateway"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.main.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "\${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# Outputs
output "api_endpoint" {
  description = "API Gateway endpoint"
  value       = aws_apigatewayv2_stage.main.invoke_url
}

output "function_name" {
  description = "Lambda function name"
  value       = aws_lambda_function.main.function_name
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

inputs = {
  aws_region    = "us-east-1"
  function_name = "my-function"
}`
  }
}

function generateBasicInfrastructure(query: string): InfrastructureResult {
  return {
    description: "basic AWS infrastructure setup",
    terraform: `# Basic AWS Infrastructure
terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name"
  type        = string
  default     = "myproject"
}

# Example resource - customize based on your needs
resource "aws_s3_bucket" "main" {
  bucket = "\${var.project_name}-bucket"

  tags = {
    Name    = "\${var.project_name}-bucket"
    Project = var.project_name
  }
}

output "bucket_name" {
  value = aws_s3_bucket.main.id
}`,
    terragrunt: `# Terragrunt Configuration
terraform {
  source = "./"
}

remote_state {
  backend = "s3"
  config = {
    bucket         = "terraform-state-\${get_aws_account_id()}"
    key            = "\${path_relative_to_include()}/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    dynamodb_table = "terraform-locks"
  }
}

inputs = {
  aws_region   = "us-east-1"
  project_name = "myproject"
}`
  }
}
