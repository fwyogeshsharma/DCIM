import React, { useState, useEffect, useRef } from 'react'
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
  ShieldCheck,
  Upload,
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

  const [certFiles, setCertFiles] = useState<{
    caCert: File | null
    clientCert: File | null
    clientKey: File | null
  }>({ caCert: null, clientCert: null, clientKey: null })

  const caRef = useRef<HTMLInputElement>(null)
  const certRef = useRef<HTMLInputElement>(null)
  const keyRef = useRef<HTMLInputElement>(null)

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

      const certs = (certFiles.caCert || certFiles.clientCert || certFiles.clientKey)
        ? {
            caCert: certFiles.caCert || undefined,
            clientCert: certFiles.clientCert || undefined,
            clientKey: certFiles.clientKey || undefined,
          }
        : undefined

      if (editingServer) {
        await api.updateServer(editingServer.id!, serverData, certs)
        toast.success('Server updated successfully')
      } else {
        await api.addServer(serverData, certs)
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
    setCertFiles({ caCert: null, clientCert: null, clientKey: null })
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
    setCertFiles({ caCert: null, clientCert: null, clientKey: null })
    setEditingServer(null)
    if (caRef.current) caRef.current.value = ''
    if (certRef.current) certRef.current.value = ''
    if (keyRef.current) keyRef.current.value = ''
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
          <h1 className="text-3xl font-bold flex items-center gap-3 text-white">
            <Server className="w-8 h-8" />
            Server Management
          </h1>
          <p className="text-gray-400 mt-2">
            Manage DCIM backend servers for multi-datacenter monitoring
          </p>
        </div>
        <Button onClick={openAddDialog} className="gap-2 bg-blue-600 text-white hover:bg-blue-700">
          <Plus className="w-4 h-4" />
          Add Server
        </Button>
      </div>

      {servers.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-16">
            <Server className="w-16 h-16 text-gray-500 mb-4" />
            <p className="text-gray-400 text-lg mb-4">No servers configured</p>
            <Button onClick={openAddDialog} className="gap-2 bg-blue-600 text-white hover:bg-blue-700">
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
                    <CardTitle className="text-xl flex items-center gap-2 text-white">
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
                    <p className="text-sm text-gray-400">URL</p>
                    <p className="text-sm font-mono truncate text-white">{server.url}</p>
                  </div>

                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant={server.metadata?.environment === 'production' ? 'default' : 'secondary'}>
                      {server.metadata?.environment || 'production'}
                    </Badge>
                    <Badge variant={server.enabled ? 'default' : 'secondary'}>
                      {server.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                    {server.hasCerts && (
                      <Badge className="bg-emerald-600 text-white hover:bg-emerald-700 gap-1">
                        <ShieldCheck className="w-3 h-3" />
                        TLS
                      </Badge>
                    )}
                  </div>

                  {server.health && (
                    <div className="flex items-center gap-2 text-sm">
                      {server.health.status === 'healthy' ? (
                        <>
                          <CheckCircle className="w-4 h-4 text-green-500" />
                          <span className="text-green-400">
                            Healthy ({server.health.responseTime}ms)
                          </span>
                        </>
                      ) : (
                        <>
                          <AlertCircle className="w-4 h-4 text-red-500" />
                          <span className="text-red-400">Offline</span>
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
                      className="flex-1 border-gray-500 text-white hover:bg-gray-700"
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
                      className="flex-1 border-gray-500 text-white hover:bg-gray-700"
                    >
                      <Edit className="w-4 h-4 mr-2" />
                      Edit
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => handleDelete(server.id!)}
                      className="bg-red-600 text-white hover:bg-red-700"
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
        <DialogContent className="max-w-md bg-white text-gray-900 border-gray-200">
          <DialogHeader>
            <DialogTitle className="text-gray-900">{editingServer ? 'Edit Server' : 'Add Server'}</DialogTitle>
            <DialogDescription className="text-gray-500">
              {editingServer
                ? 'Update the server configuration'
                : 'Add a new DCIM backend server to monitor'}
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={handleSubmit}>
            <div className="max-h-[70vh] overflow-y-auto space-y-4 py-4 pr-1">
              <div className="space-y-2">
                <Label htmlFor="name" className="text-gray-700">Server Name *</Label>
                <Input
                  id="name"
                  placeholder="DC-East"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  required
                  className="bg-white border-gray-300 text-gray-900 placeholder:text-gray-400"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="url" className="text-gray-700">Server URL *</Label>
                <Input
                  id="url"
                  placeholder="http://192.168.1.100:8080/api/v1"
                  value={formData.url}
                  onChange={(e) => setFormData({ ...formData, url: e.target.value })}
                  required
                  className="bg-white border-gray-300 text-gray-900 placeholder:text-gray-400"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="location" className="text-gray-700">Location</Label>
                <Input
                  id="location"
                  placeholder="New York"
                  value={formData.location}
                  onChange={(e) => setFormData({ ...formData, location: e.target.value })}
                  className="bg-white border-gray-300 text-gray-900 placeholder:text-gray-400"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="environment" className="text-gray-700">Environment</Label>
                <select
                  id="environment"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md bg-white text-gray-900"
                  value={formData.environment}
                  onChange={(e) => setFormData({ ...formData, environment: e.target.value })}
                >
                  <option value="production">Production</option>
                  <option value="staging">Staging</option>
                  <option value="development">Development</option>
                </select>
              </div>

              <div className="space-y-2">
                <Label htmlFor="color" className="text-gray-700">Color Tag</Label>
                <div className="flex gap-2">
                  <Input
                    id="color"
                    type="color"
                    value={formData.color}
                    onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                    className="w-20 h-10 bg-white border-gray-300"
                  />
                  <Input
                    value={formData.color}
                    onChange={(e) => setFormData({ ...formData, color: e.target.value })}
                    placeholder="#3b82f6"
                    className="flex-1 bg-white border-gray-300 text-gray-900 placeholder:text-gray-400"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2">
                <Switch
                  id="enabled"
                  checked={formData.enabled}
                  onCheckedChange={(checked) => setFormData({ ...formData, enabled: checked })}
                />
                <Label htmlFor="enabled" className="text-gray-700">Enable server</Label>
              </div>

              {/* TLS Certificates Section */}
              <div className="border border-gray-200 rounded-lg p-4 space-y-3 bg-gray-50">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-4 h-4 text-emerald-600" />
                  <Label className="text-gray-700 font-semibold text-sm">TLS Certificates</Label>
                  <span className="text-xs text-gray-400">(optional)</span>
                </div>
                <p className="text-xs text-gray-500">
                  Upload per-server TLS certificates for mTLS connections.
                  {editingServer?.hasCerts && (
                    <span className="text-emerald-600 font-medium"> This server already has certificates uploaded. Uploading new files will overwrite them.</span>
                  )}
                </p>

                <div className="space-y-2">
                  <Label htmlFor="caCert" className="text-gray-600 text-xs">CA Certificate (ca.crt)</Label>
                  <Input
                    ref={caRef}
                    id="caCert"
                    type="file"
                    accept=".crt,.pem,.cer"
                    onChange={(e) => setCertFiles({ ...certFiles, caCert: e.target.files?.[0] || null })}
                    className="bg-white border-gray-300 text-gray-700 text-sm file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:bg-gray-200 file:text-gray-700 hover:file:bg-gray-300"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="clientCert" className="text-gray-600 text-xs">Client Certificate (client.crt)</Label>
                  <Input
                    ref={certRef}
                    id="clientCert"
                    type="file"
                    accept=".crt,.pem,.cer"
                    onChange={(e) => setCertFiles({ ...certFiles, clientCert: e.target.files?.[0] || null })}
                    className="bg-white border-gray-300 text-gray-700 text-sm file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:bg-gray-200 file:text-gray-700 hover:file:bg-gray-300"
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="clientKey" className="text-gray-600 text-xs">Client Key (client.key)</Label>
                  <Input
                    ref={keyRef}
                    id="clientKey"
                    type="file"
                    accept=".key,.pem"
                    onChange={(e) => setCertFiles({ ...certFiles, clientKey: e.target.files?.[0] || null })}
                    className="bg-white border-gray-300 text-gray-700 text-sm file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-sm file:bg-gray-200 file:text-gray-700 hover:file:bg-gray-300"
                  />
                </div>
              </div>
            </div>

            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setShowDialog(false)} className="border-gray-300 text-gray-700 hover:bg-gray-100">
                Cancel
              </Button>
              <Button type="submit" className="bg-blue-600 text-white hover:bg-blue-700">{editingServer ? 'Update' : 'Add'} Server</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  )
}
