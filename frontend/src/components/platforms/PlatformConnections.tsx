import React, { useEffect, useState } from 'react'
import { Link2, Link2Off, AlertTriangle, CheckCircle2, Clock } from 'lucide-react'
import { oauthApi, type Platform, type PlatformStatus } from '../../lib/api'
import { Spinner } from '../ui/shared'
import toast from 'react-hot-toast'

const PLATFORM_META: Record<Platform, { name: string; icon: string; color: string; bgGrad: string }> = {
  tiktok: {
    name: 'TikTok',
    icon: '🎵',
    color: 'text-white',
    bgGrad: 'from-[#010101] to-[#1a1a1a]',
  },
  instagram: {
    name: 'Instagram',
    icon: '📸',
    color: 'text-pink-300',
    bgGrad: 'from-purple-950/60 to-pink-950/60',
  },
  youtube: {
    name: 'YouTube',
    icon: '▶',
    color: 'text-red-300',
    bgGrad: 'from-red-950/50 to-[#1a0808]',
  },
}

function PlatformCard({ status, onDisconnect }: {
  status: PlatformStatus
  onDisconnect: (p: Platform) => void
}) {
  const meta = PLATFORM_META[status.platform]
  const [disconnecting, setDisconnecting] = useState(false)

  async function handleDisconnect() {
    setDisconnecting(true)
    try {
      await oauthApi.disconnect(status.platform)
      onDisconnect(status.platform)
      toast.success(`${meta.name} disconnected`)
    } catch {
      toast.error('Failed to disconnect')
    } finally {
      setDisconnecting(false)
    }
  }

  return (
    <div className={`relative rounded-xl border border-forge-border bg-gradient-to-br ${meta.bgGrad} p-5 flex flex-col gap-4 animate-slide-up`}>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className="text-2xl">{meta.icon}</span>
          <div>
            <p className={`font-display font-bold text-base ${meta.color}`}>{meta.name}</p>
            {status.account?.platform_username && (
              <p className="text-forge-dim text-xs font-mono">@{status.account.platform_username}</p>
            )}
          </div>
        </div>

        {/* Connection indicator */}
        {status.connected ? (
          <div className="flex items-center gap-1.5 text-forge-green text-xs font-mono">
            <CheckCircle2 size={14} />
            Connected
          </div>
        ) : (
          <div className="flex items-center gap-1.5 text-forge-dim text-xs font-mono">
            <div className="w-2 h-2 rounded-full bg-forge-dim" />
            Not connected
          </div>
        )}
      </div>

      {/* Pending approval warning */}
      {status.pending_approval && (
        <div className="flex items-start gap-2 bg-forge-yellow/5 border border-forge-yellow/20 rounded-lg p-3">
          <AlertTriangle size={14} className="text-forge-yellow mt-0.5 shrink-0" />
          <p className="text-forge-yellow text-xs leading-relaxed">
            Requires platform app approval before connecting.
            {!status.configured && ' Credentials also missing in .env.'}
          </p>
        </div>
      )}

      {/* Not configured warning */}
      {!status.configured && !status.pending_approval && (
        <div className="flex items-start gap-2 bg-forge-red/5 border border-forge-red/20 rounded-lg p-3">
          <AlertTriangle size={14} className="text-forge-red mt-0.5 shrink-0" />
          <p className="text-forge-red text-xs leading-relaxed">
            Credentials not configured. Add to your .env file.
          </p>
        </div>
      )}

      {/* Connected info */}
      {status.connected && status.account && (
        <div className="flex items-center gap-1.5 text-forge-dim text-xs">
          <Clock size={12} />
          <span>Connected {new Date(status.account.connected_at).toLocaleDateString()}</span>
        </div>
      )}

      {/* Actions */}
      <div className="mt-auto pt-1">
        {status.connected ? (
          <button
            onClick={handleDisconnect}
            disabled={disconnecting}
            className="btn-danger w-full flex items-center justify-center gap-2"
          >
            {disconnecting ? <Spinner size={14} /> : <Link2Off size={14} />}
            Disconnect
          </button>
        ) : (
          <button
            onClick={() => oauthApi.connect(status.platform)}
            disabled={!status.configured}
            className="btn-primary w-full flex items-center justify-center gap-2"
          >
            <Link2 size={14} />
            Connect {meta.name}
          </button>
        )}
      </div>
    </div>
  )
}

export default function PlatformConnections() {
  const [statuses, setStatuses] = useState<PlatformStatus[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    oauthApi.statuses()
      .then(r => setStatuses(r.data))
      .finally(() => setLoading(false))
  }, [])

  function handleDisconnect(platform: Platform) {
    setStatuses(prev =>
      prev.map(s => s.platform === platform
        ? { ...s, connected: false, account: null }
        : s
      )
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Spinner size={24} />
      </div>
    )
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      {statuses.map(s => (
        <PlatformCard key={s.platform} status={s} onDisconnect={handleDisconnect} />
      ))}
    </div>
  )
}
