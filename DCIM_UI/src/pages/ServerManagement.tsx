import React, { useState, useEffect } from 'react'
import { api } from '@/lib/api'
import type { ServerConfig } from '@/lib/types'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Server,
  Plus,
  Trash2,
  Edit,
  CheckCircle,
  XCircle,
  RefreshCw,
  AlertCircle,
} from 'lucide-react'
import toast from 'react-hot-toast'

export default function ServerManagement() {
  const [servers, setServers] = useState<ServerConfig[]>([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [editingServer, setEditingServer] = useState<ServerConfig | null>(null)
  const [testingServer, setTestingServer] = useState<string | null>(null)

  const [formData, setFormData] = useState({
    name: '',
    url: '',
    enabled: true,
    location: '',
    environment: 'production',
    color: '#3b82f6',
  })

  useEffect(() => {
    loadServers()
  }, [])

  const loadServers = async () => {
    try {
      const data = await api.getServers()
      setServers(data)
    } catch (error: any) {
      toast.error('Failed to load servers: ' + error.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    try {
      const serverData = {
        name: formData.name,
        url: formData.url,
        enabled: formData.enabled,
        metadata: {
          location: formData.location,
          environment: formData.environment,
          color: formData.color,
        },
      }

      if (editingServer) {
        await api.updateServer(editingServer.id!, serverData)
        toast.success('Server updated successfully')
      } else {
        await api.addServer(serverData)
        toast.success('Server added successfully')
      }

      setShowDialog(false)
      resetForm()
      loadServers()
    } catch (error: any) {
      toast.error('Failed to save server: ' + error.message)
    }
  }

  const handleEdit = (server: ServerConfig) => {
    setEditingServer(server)
    setFormData({
      name: server.name,
      url: server.url,
      enabled: server.enabled !== false,
      location: server.metadata?.location || '',
      environment: server.metadata?.environment || 'production',
      color: server.metadata?.color || '#3b82f6',
    })
    setShowDialog(true)
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Are you sure you want to delete this server?')) return

    try {
      await api.deleteServer(id)
      toast.success('Server deleted')
      loadServers()
    } catch (error: any) {
      toast.error('Failed to delete server: ' + error.message)
    }
  }

  const handleToggleStatus = async (id: string, enabled: boolean) => {
    try {
      await api.toggleServerStatus(id, enabled)
      toast.success(`Server ${enabled ? 'enabled' : 'disabled'}`)
      loadServers()
    } catch (error: any) {
      toast.error('Failed to toggle server status: ' + error.message)
    }
  }

  const handleTestConnection = async (id: string) => {
    setTestingServer(id)
    try {
      const result = await api.testServerConnection(id)
      if (result.status === 'healthy') {
        toast.success(`Server is healthy (${result.responseTime}ms)`)
      } else {
        toast.error(`Server is offline: ${result.error}`)
      }
      loadServers()
    } catch (error: any) {
      toast.error('Connection test failed: ' + error.message)
    } finally {
      setTestingServer(null)
    }
  }

  const resetForm = () => {
    setFormData({
      name: '',
      url: '',
      enabled: true,
      location: '',
      environment: 'production',
      color: '#3b82f6',
    })
    setEditingServer(null)
  }

  const openAddDialog = () => {
    resetForm()
    setShowDialog(true)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div className="container mx-auto p-6 max-w-7xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <Server className="w-8 h-8" />
            Server Management
          </h1>
          <p className="text-gray-600 mt-2">
            Manage DCIM backend servers for multi-datacenter monitoring
          </p>
        </div>
        <Button onClick={openAddDialog} className="gap-2">
          <Plus className="w-4 h-4" />
          Add Server
        </Button>
      </div>

      {servers.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <Server className="w-16 h-16 text-gray-300 mb-4" />
            <p className="text-gray-600 text-lg mb-4">No servers configured</p>
            <Button onClick={openAddDialog} className="gap-2">
              <Plus className="w-4 h-4" />
              Add Your First Server
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {servers.map((server) => (
            <Card key={server.id} className="relative">
              <div
                className="absolute top-0 left-0 right-0 h-1 rounded-t-lg"
                style={{ backgroundColor: server.metadata?.color || '#3b82f6' }}
              />
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <CardTitle className="text-xl flex items-center gap-2">
                      {server.name}
                      {server.health?.status === 'healthy' ? (
                        <CheckCircle className="w-5 h-5 text-green-500" />
                      ) : (
                        <XCircle className="w-5 h-5 text-red-500" />
                      )}
                    </CardTitle>
                    <CardDescription className="mt-1">
                      {server.metadata?.location || 'No location'}
                    </CardDescription>
                  </div>
                  <Switch
                    checked={server.enabled !== false}
                    onCheckedChange={(checked) => handleToggleStatus(server.id!, checked)}
                  />
                </div>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  <div>
                    <p className="text-sm text-gray-500">URL</p>
                    <p className="text-sm font-mono truncate">{server.url}</p>
                  </div>

                  <div className="flex items-center gap-2">
                    <Badge variant={server.metadata?.environment === 'production' ? 'default' : 'secondary'}>
                      {server.metadata?.environment || 'production'}
                    </Badge>
                    <Badge variant={server.enabled ? 'default' : 'secondary'}>
                      {server.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  </div>

                  {server.health && (
                    <div className="flex items-center gap-2 text-sm">
                      {server.health.status === 'healthy' ? (
                        <>
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-green-600">
                            Healthy ({server.health.responseTime}ms)
                          </span>
                        </>
                      ) : (
                        <>
                          <AlertCircle className="w-4 h-4 text-red-500" />
                          <span className="text-red-600">Offline</span>
                        </>
                      )}
                    </div>
                  )}

                  <div className="flex gap-2 pt-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleTestConnection(server.id!)}
                      disabled={testingServer === server.id}
                      className="flex-1"
                    >
                      {testingServer === server.id ? (
                        <RefreshCw className="w-4 h-4 animate-spin" />
                      ) : (
                        <>
                          <RefreshCw className="w-4 h-4 mr-2" />
                          Test
                        </>
                      )}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleEdit(server)}
                      className="flex-1"
                    >
                      <Edit className="w-4 h-4 mr-2" />
                      Edit
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => handleDelete(server.id!)}
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={showDialog} onOpenChange={setShowDialog}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{editingServer ? 'Edit Server' : 'Add Server'}</DialogTitle>
            <DialogDescription>
              {editingServer
                ? 'Update the server configuration'
                : 'Add a new DCIM backend server to monitor'}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSubmit}>
            <div className="space-y-4 py-4">
              <div className="space-y-2">
                <Label htmlFor="name">Server Name *</Label>
                <Input
                  id="name"
                  placeholder="DC-East"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="url">Server URL *</Label>
                <Input
                  id="url"
                  placeholder="http://192.168.1.100:8080/api/v1"
                  value={formData.url}
                  onChange={(e) => setFormData({ ...formData, url: e.target.value })}
                  required
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="location">Location</Label>
                <Input
                  id="location"
                  placeholder="New York"
                  value={formData.location}
                  onChange={(e) => setFormData({ ...formData, location: e.target.value })}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="environment">Environment</Label>
                <select
                  id="environment"
                  className="w-full px-3 py-2 border rounded-md"
                  value={formData.environment}
                  onChange={(e) => setFormData({ ...formData, environment: e.target.value })}
                >
                  <option value="production">Production</option>
                  <option value="staging">Staging</option>
                  <option value="development">Development</option>
                </select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="color">Color Tag</Label>
                <div className="flex gap-2">
                  <Input
                    id="color"
                    type="color"
                    value={formData.color}
                    onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                    className="w-20 h-10"
                  />
                  <Input
                    value={formData.color}
                    onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                    placeholder="#3b82f6"
                    className="flex-1"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  id="enabled"
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label htmlFor="enabled">Enable server</Label>
              </div>
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setShowDialog(false)}>
                Cancel
              </Button>
              <Button type="submit">{editingServer ? 'Update' : 'Add'} Server</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
