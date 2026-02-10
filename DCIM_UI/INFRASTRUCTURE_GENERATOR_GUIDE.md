# 🚀 AI Infrastructure as Code Generator

## Overview
An AI-powered tool that converts natural language descriptions into production-ready **Terraform** and **Terragrunt** configurations.

## ✨ Features

- **Natural Language Input**: Describe infrastructure in plain English
- **Dual Output**: Generates both Terraform and Terragrunt files
- **Multi-Cloud Support**: AWS, Azure, GCP, and more
- **Production-Ready**: Follows best practices and security standards
- **Instant Download**: Download generated files directly
- **Code Preview**: View configurations before downloading

## 🎯 How to Use

### 1. Start the DCIM Server
```bash
cd E:\Projects\DCIM\DCIM_Server
.\dcim-server.exe
```

### 2. Navigate to the Generator
Open your browser and go to: `http://localhost:5173/app/nl-query`

### 3. Describe Your Infrastructure
Type what you need in plain English. Examples:

#### AWS Examples
- "Create a VPC with 3 public and 3 private subnets across availability zones"
- "Setup an ECS cluster with ALB and auto-scaling"
- "Deploy RDS PostgreSQL with read replicas"
- "Configure S3 bucket with CloudFront distribution"

#### Kubernetes Examples
- "Create a Kubernetes cluster with monitoring stack"
- "Setup EKS with Istio service mesh"
- "Deploy ArgoCD for GitOps workflow"

#### Multi-Tier Applications
- "Build 3-tier web app with load balancer, app servers, and database"
- "Create microservices infrastructure with API gateway"
- "Setup serverless architecture with Lambda and API Gateway"

#### Networking & Security
- "Configure secure VPC with bastion host and NAT gateway"
- "Setup VPN connection between on-prem and cloud"
- "Create security groups for web, app, and database tiers"

#### CI/CD Infrastructure
- "Setup Jenkins on Kubernetes with persistent storage"
- "Create GitLab CI runners with Docker support"
- "Deploy Argo Workflows for ML pipelines"

### 4. Review & Download
- ✅ AI generates configurations instantly
- 📄 View **main.tf** (Terraform) in purple panel
- 📄 View **terragrunt.hcl** (Terragrunt) in green panel
- 💾 Click download buttons to save files

## 📋 Best Practices

### Be Specific
❌ **Vague**: "Create a server"
✅ **Clear**: "Create AWS EC2 t3.medium instance with Amazon Linux 2, SSH access, and CloudWatch monitoring"

### Include Requirements
✅ **Good**: "Create VPC with **10.0.0.0/16** CIDR, **3 AZs**, **public/private subnets**, **NAT gateway**"

### Specify Cloud Provider
✅ "Create **AWS** Lambda function with **Python 3.9** runtime"

### Mention Environment
✅ "Setup **production** Kubernetes cluster with **3 master nodes** and **5 worker nodes**"

## 🎨 Example Queries

### Simple Infrastructure
```
Create an AWS S3 bucket with versioning enabled
```

### Complex Setup
```
Build a production-ready 3-tier web application on AWS:
- Application Load Balancer in public subnets
- Auto-scaling group of EC2 instances (t3.medium) in private subnets
- RDS PostgreSQL 14 in private database subnets
- ElastiCache Redis cluster for session storage
- CloudFront distribution for static assets
- Route53 for DNS management
- Enable CloudWatch monitoring and logs
```

### Kubernetes
```
Create an EKS cluster with:
- 3 availability zones
- Managed node groups (t3.large, 2-10 nodes)
- AWS Load Balancer Controller
- EBS CSI driver for persistent volumes
- Metrics server for HPA
- Cluster Autoscaler
```

## 📁 Output Files

### main.tf (Terraform)
- Provider configuration
- Resource definitions
- Variables and outputs
- Best practice comments

### terragrunt.hcl (Terragrunt)
- Remote state configuration
- Input variables
- Dependencies
- Environment-specific settings

## 🔧 Technical Details

- **AI Model**: Nvidia Llama 3.1 Nemotron 70B
- **Temperature**: 0.2 (focused, deterministic)
- **Max Tokens**: 3000 (comprehensive configs)
- **API**: Proxied through DCIM server (no CORS issues)

## ⚠️ Important Notes

1. **Review Before Applying**: Always review generated configs
2. **Test First**: Apply in dev/staging before production
3. **Customize**: Adjust names, sizes, and settings to your needs
4. **Security**: Update default passwords and keys
5. **Costs**: Review cloud costs before deployment

## 🐛 Troubleshooting

### "Error processing query"
**Solution**: Ensure DCIM server is running on port 8443

### "No configs generated"
**Solution**: Try rephrasing your request with more detail

### Server not accessible
**Check**:
```bash
netstat -ano | findstr :8443
```
Should show the DCIM server listening

## 💡 Tips

1. **Start Simple**: Begin with basic requests, then add complexity
2. **Iterate**: Refine configs by asking follow-up questions
3. **Combine Features**: Request multiple related resources together
4. **Use Variables**: Ask for parameterized configs for reusability
5. **Add Tags**: Request proper resource tagging for organization

## 🚀 Next Steps

After downloading:
1. Create project directory
2. Place files in appropriate locations
3. Initialize Terraform: `terraform init`
4. Review plan: `terraform plan`
5. Apply: `terraform apply`

## 🎓 Learning Resources

- [Terraform Documentation](https://www.terraform.io/docs)
- [Terragrunt Documentation](https://terragrunt.gruntwork.io/)
- [AWS Provider](https://registry.terraform.io/providers/hashicorp/aws/latest/docs)
- [Azure Provider](https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs)
- [GCP Provider](https://registry.terraform.io/providers/hashicorp/google/latest/docs)

---

**Happy Infrastructure Coding! 🎉**
