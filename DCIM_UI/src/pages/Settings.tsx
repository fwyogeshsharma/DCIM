import { useUIStore } from '@/stores/useUIStore'
import { Button } from '@/components/ui/button'

export default function Settings() {
  const { theme, toggleTheme, timeRange, setTimeRange } = useUIStore()

  const timeRanges = [
    { value: '5m', label: '5 minutes' },
    { value: '1h', label: '1 hour' },
    { value: '6h', label: '6 hours' },
    { value: '24h', label: '24 hours' },
    { value: '7d', label: '7 days' },
    { value: '30d', label: '30 days' },
  ] as const

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-4xl font-bold text-white">Settings</h1>
        <p className="text-slate-400 mt-2 text-lg">
          Configure your monitoring preferences
        </p>
      </div>

      <div className="space-y-6 max-w-2xl">
        {/* Theme Settings */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">Appearance</h3>
          <div className="flex items-center justify-between">
            <div>
              <p className="font-medium text-white">Theme</p>
              <p className="text-sm text-slate-400">
                Choose your preferred color scheme
              </p>
            </div>
            <Button
              onClick={toggleTheme}
              className="bg-blue-600 hover:bg-blue-700 text-white border-0"
            >
              {theme === 'dark' ? 'Dark' : 'Light'}
            </Button>
          </div>
        </div>

        {/* Time Range Settings */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">Default Time Range</h3>
          <div className="grid grid-cols-3 gap-3">
            {timeRanges.map((range) => (
              <Button
                key={range.value}
                onClick={() => setTimeRange(range.value)}
                className={`w-full transition-all duration-200 ${
                  timeRange === range.value
                    ? 'bg-blue-600 text-white hover:bg-blue-700 border-0'
                    : 'bg-slate-700/50 text-slate-300 hover:bg-slate-700 border border-white/10'
                }`}
              >
                {range.label}
              </Button>
            ))}
          </div>
        </div>

        {/* License Information */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">License Information</h3>
          <div className="space-y-3 text-sm">
            <div className="flex justify-between items-center p-3 bg-slate-700/30 rounded-lg">
              <span className="text-slate-400">Status:</span>
              <span className="font-medium text-green-400 inline-flex items-center px-3 py-1 rounded-full bg-green-500/20 border border-green-500/30">
                Active
              </span>
            </div>
            <div className="flex justify-between items-center p-3 bg-slate-700/30 rounded-lg">
              <span className="text-slate-400">Max Agents:</span>
              <span className="font-medium text-white">Unlimited</span>
            </div>
            <div className="flex justify-between items-center p-3 bg-slate-700/30 rounded-lg">
              <span className="text-slate-400">Expires:</span>
              <span className="font-medium text-white">Never</span>
            </div>
          </div>
        </div>

        {/* API Settings */}
        <div className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 hover:border-white/20 transition-all duration-300">
          <h3 className="text-xl font-semibold mb-4 text-white">API Configuration</h3>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium text-slate-300 block mb-2">API Endpoint</label>
              <input
                type="text"
                value="/api/v1"
                readOnly
                className="w-full px-4 py-3 rounded-lg border border-white/10 bg-slate-900/50 text-slate-300 text-sm font-mono"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-slate-300 block mb-2">SSE Endpoint</label>
              <input
                type="text"
                value="/api/v1/events"
                readOnly
                className="w-full px-4 py-3 rounded-lg border border-white/10 bg-slate-900/50 text-slate-300 text-sm font-mono"
              />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
