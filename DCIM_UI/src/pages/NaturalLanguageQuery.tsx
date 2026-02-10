import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Send, Download, FileCode, Loader2 } from 'lucide-react'
import { generateInfrastructure } from '@/lib/infrastructure-templates'

interface GeneratedConfig {
  terraform?: string
  terragrunt?: string
}

export default function NaturalLanguageQuery() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([])
  const [generatedConfigs, setGeneratedConfigs] = useState<GeneratedConfig>({})
  const [isGenerating, setIsGenerating] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return

    setMessages((prev) => [...prev, { role: 'user', content: query }])
    setIsGenerating(true)

    // Simulate thinking time for better UX
    setTimeout(() => {
      try {
        const result = generateInfrastructure(query)

        setGeneratedConfigs({
          terraform: result.terraform,
          terragrunt: result.terragrunt
        })

        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: `✅ Generated ${result.description}! Check the side panel to download your Terraform and Terragrunt configurations.`
          }
        ])
        setQuery('')
      } catch (error) {
        setMessages((prev) => [
          ...prev,
          {
            role: 'assistant',
            content: '❌ Could not generate configuration. Try being more specific (e.g., "Create AWS VPC" or "Setup Kubernetes cluster")'
          }
        ])
      } finally {
        setIsGenerating(false)
      }
    }, 800) // Slight delay to feel more natural
  }

  const downloadFile = (content: string, filename: string) => {
    const blob = new Blob([content], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div>
        <h1 className="text-3xl font-bold text-white">🚀 AI Infrastructure as Code Generator</h1>
        <p className="text-slate-400 mt-2">
          Describe your infrastructure needs and get production-ready Terraform & Terragrunt configurations
        </p>
      </div>

      <div className="flex-1 flex gap-4 min-h-0">
        {/* Chat Section */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-6 overflow-y-auto">
            {messages.length === 0 ? (
              <div className="text-slate-400 text-center py-12">
                <p className="text-lg mb-4 text-white">💡 Describe your infrastructure requirements</p>
                <div className="space-y-2 text-sm">
                  <p>💻 "Create a 3-tier web application on AWS with load balancer"</p>
                  <p>☸️ "Setup a Kubernetes cluster with monitoring and logging"</p>
                  <p>🌐 "Deploy a microservices architecture on Azure"</p>
                  <p>🔒 "Configure a secure VPC with public and private subnets"</p>
                  <p>📦 "Build CI/CD infrastructure with GitLab runners"</p>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {messages.map((message, index) => (
                  <div
                    key={index}
                    className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[80%] rounded-lg p-4 ${
                        message.role === 'user'
                          ? 'bg-blue-500 text-white'
                          : 'bg-slate-700/50 text-white'
                      }`}
                    >
                      <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                    </div>
                  </div>
                ))}
                {isGenerating && (
                  <div className="flex justify-start">
                    <div className="bg-slate-700/50 text-white rounded-lg p-4">
                      <div className="flex items-center gap-2">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        <p className="text-sm">Generating infrastructure configurations...</p>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>

          <form onSubmit={handleSubmit} className="flex gap-2 mt-4">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Describe your infrastructure needs (e.g., 'Create AWS VPC with 3 subnets')..."
              className="flex-1 px-4 py-2 rounded-lg border border-white/10 bg-slate-800/50 text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              disabled={isGenerating}
            />
            <Button type="submit" disabled={isGenerating || !query.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </div>

        {/* Generated Configs Panel */}
        {(generatedConfigs.terraform || generatedConfigs.terragrunt) && (
          <div className="w-96 flex flex-col gap-4 min-h-0">
            {/* Terraform Config */}
            {generatedConfigs.terraform && (
              <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4 flex flex-col min-h-0">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <FileCode className="w-5 h-5 text-purple-400" />
                    <h3 className="text-lg font-semibold text-white">main.tf</h3>
                  </div>
                  <button
                    onClick={() => downloadFile(generatedConfigs.terraform!, 'main.tf')}
                    className="flex items-center gap-2 px-3 py-1.5 bg-purple-500/20 hover:bg-purple-500/30 border border-purple-500/30 text-purple-400 rounded-lg transition-colors text-sm"
                  >
                    <Download className="w-4 h-4" />
                    Download
                  </button>
                </div>
                <pre className="flex-1 bg-slate-900/50 rounded-lg p-4 overflow-auto text-xs text-slate-300 font-mono">
                  {generatedConfigs.terraform}
                </pre>
              </div>
            )}

            {/* Terragrunt Config */}
            {generatedConfigs.terragrunt && (
              <div className="flex-1 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-lg p-4 flex flex-col min-h-0">
                <div className="flex items-center justify-between mb-3">
                  <div className="flex items-center gap-2">
                    <FileCode className="w-5 h-5 text-green-400" />
                    <h3 className="text-lg font-semibold text-white">terragrunt.hcl</h3>
                  </div>
                  <button
                    onClick={() => downloadFile(generatedConfigs.terragrunt!, 'terragrunt.hcl')}
                    className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 hover:bg-green-500/30 border border-green-500/30 text-green-400 rounded-lg transition-colors text-sm"
                  >
                    <Download className="w-4 h-4" />
                    Download
                  </button>
                </div>
                <pre className="flex-1 bg-slate-900/50 rounded-lg p-4 overflow-auto text-xs text-slate-300 font-mono">
                  {generatedConfigs.terragrunt}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
