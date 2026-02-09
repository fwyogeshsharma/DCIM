import { useState } from 'react'
import { useNLQuery } from '@/hooks/useNLQuery'
import { Button } from '@/components/ui/button'
import { Send } from 'lucide-react'

export default function NaturalLanguageQuery() {
  const [query, setQuery] = useState('')
  const [messages, setMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([])
  const nlQuery = useNLQuery()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim()) return

    // Add user message
    setMessages((prev) => [...prev, { role: 'user', content: query }])

    try {
      const result = await nlQuery.mutateAsync(query)

      // Add assistant response
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `${result.explanation}\n\nFound ${result.data.length} results.`,
        },
      ])

      setQuery('')
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: 'Sorry, I encountered an error processing your query.',
        },
      ])
    }
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      <div>
        <h1 className="text-3xl font-bold">Natural Language Query</h1>
        <p className="text-muted-foreground mt-2">
          Ask questions about your infrastructure in plain English
        </p>
      </div>

      <div className="flex-1 bg-card border border-border rounded-lg p-6 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="text-muted-foreground text-center py-12">
            <p className="text-lg mb-4">Ask me anything about your infrastructure</p>
            <div className="space-y-2 text-sm">
              <p>Try: "Show me agents with CPU above 80% in the last hour"</p>
              <p>Or: "Which agents have the most alerts this week?"</p>
              <p>Or: "What's the average temperature of all agents?"</p>
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
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted'
                  }`}
                >
                  <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Ask a question..."
          className="flex-1 px-4 py-2 rounded-lg border border-border bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          disabled={nlQuery.isPending}
        />
        <Button type="submit" disabled={nlQuery.isPending || !query.trim()}>
          <Send className="h-4 w-4" />
        </Button>
      </form>
    </div>
  )
}
