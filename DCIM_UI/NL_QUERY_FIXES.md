# Natural Language Query - Fixes & Updates

## ✅ Issues Fixed

### 1. **Removed OpenAI Dependency**
   - ❌ Old: Used OpenAI API (causing 401 errors)
   - ✅ New: Uses Nvidia Nemotron 70B model exclusively

### 2. **Dual-Mode AI System**
   - **Infrastructure Generation Mode**: Generates Terraform/Terragrunt configs
   - **Monitoring Mode**: Answers questions about your DCIM system

### 3. **Smart Query Detection**
   - Automatically detects if you're asking about:
     - Infrastructure generation (terraform, deploy, provision, etc.)
     - System monitoring (alerts, warnings, CPU, metrics, etc.)

### 4. **Server Connection Handling**
   - Gracefully handles server unavailability
   - Provides helpful error messages

## 🚀 How to Use

### Infrastructure Generation Queries
Ask questions like:
- "Generate Terraform config for a 3-tier web application on AWS"
- "Create infrastructure for a Kubernetes cluster with monitoring"
- "Setup a production environment with load balancer and auto-scaling"
- "Deploy a microservices architecture on Azure"

**Result**: Generates downloadable Terraform and Terragrunt files in the side panel

### Monitoring Queries
Ask questions like:
- "What are the latest warnings?"
- "Show me agents with high CPU usage"
- "Which agents have critical alerts?"
- "What's the current system health?"

**Result**: AI analyzes your actual system data and provides insights

## 🔧 Technical Details

### API Configuration
- **API Provider**: Nvidia NIM (NVIDIA Inference Microservices)
- **Model**: nvidia/llama-3.1-nemotron-70b-instruct
- **API Key**: Pre-configured (embedded in code)
- **Endpoint**: https://integrate.api.nvidia.com/v1/chat/completions

### Features
1. **Real-time Data Analysis**: Fetches live agent and alert data
2. **Context-Aware Responses**: AI understands your DCIM system state
3. **Dual Output**: Chat responses + downloadable config files
4. **Error Handling**: Graceful fallbacks when servers are unavailable

## 🐛 Troubleshooting

### "Cannot connect to DCIM server"
**Solution**: Ensure the DCIM server is running:
```bash
cd E:\Projects\DCIM\DCIM_Server
.\dcim-server.exe
```

### "503 Service Unavailable"
**Cause**: Proxy can't connect to DCIM server
**Check**:
1. DCIM server running on port 8443
2. Proxy server running on port 3001
3. mTLS certificates are valid

### No response from AI
**Solution**: Check browser console for API errors
- Verify Nvidia API key is valid
- Check internet connection

## 📊 Response Examples

### Monitoring Query: "What are the latest warnings?"
```
Based on current system status:
- Total Agents: 3
- Online: 2, Offline: 1
- Active Alerts: 5 (2 critical, 3 warnings)

Latest Warnings:
1. Agent "server-01": High CPU usage (85%)
2. Agent "server-02": Disk space low (12% remaining)
3. Agent "server-03": Memory usage elevated (78%)

Recommended actions:
- Investigate CPU spike on server-01
- Free disk space on server-02
- Monitor memory on server-03
```

### Infrastructure Query: "Create AWS VPC with subnets"
**Result**: Generates two downloadable files:
- `main.tf` - Complete Terraform configuration
- `terragrunt.hcl` - Terragrunt wrapper configuration

## 🎯 Best Practices

1. **Be specific**: "Create AWS VPC with 3 public and 3 private subnets" is better than "Create a network"
2. **Include requirements**: Mention cloud provider, regions, sizing, etc.
3. **Review configs**: Always review generated Terraform before applying
4. **Test first**: Apply configs in a test environment first

## 📝 Notes

- The AI has access to your real-time system data for monitoring queries
- Infrastructure generation doesn't require system access
- All queries are processed through Nvidia's secure API
- Generated configs follow industry best practices
- Configs include comments explaining key decisions
