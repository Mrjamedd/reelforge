import React, { useEffect, useState } from 'react'
import { TrendingUp, Film, Send, AlertCircle } from 'lucide-react'
import { jobsApi, type DashboardStats } from '../../lib/api'
import { Spinner } from '../ui/shared'

function StatCard({ icon, label, value, accent = false }: {
  icon: React.ReactNode
  label: string
  value: number | string
  accent?: boolean
}) {
  return (
    <div className={`card flex items-center gap-4 animate-slide-up ${accent ? 'border-forge-accent/30' : ''}`}>
      <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${accent ? 'bg-forge-accent/15' : 'bg-forge-muted'}`}>
        <span className={accent ? 'text-forge-accent' : 'text-forge-dim'}>{icon}</span>
      </div>
      <div>
        <p className="text-forge-dim text-xs font-display uppercase tracking-wider">{label}</p>
        <p className="font-display font-bold text-2xl text-forge-text leading-none mt-0.5">{value}</p>
      </div>
    </div>
  )
}

export default function StatsOverview({ refreshKey }: { refreshKey: number }) {
  const [stats, setStats] = useState<DashboardStats | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    jobsApi.stats()
      .then(r => setStats(r.data))
      .finally(() => setLoading(false))
  }, [refreshKey])

  if (loading) return <div className="flex items-center h-24 justify-center"><Spinner /></div>
  if (!stats) return null

  const postedCount = stats.jobs_by_status['posted'] ?? 0
  const failedCount = stats.jobs_by_status['failed'] ?? 0

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <StatCard icon={<Film size={18} />}        label="Uploads"      value={stats.total_uploads} />
      <StatCard icon={<Send size={18} />}         label="Total Jobs"   value={stats.total_jobs} />
      <StatCard icon={<TrendingUp size={18} />}   label="Posted"       value={postedCount} accent />
      <StatCard icon={<AlertCircle size={18} />}  label="Failed"       value={failedCount} />
    </div>
  )
}
