import React, { useState } from 'react'
import { LogOut, RefreshCw, Film, Link2, Clock } from 'lucide-react'
import { useAuthStore } from '../hooks/useAuthStore'
import StatsOverview from '../components/dashboard/StatsOverview'
import JobsTable from '../components/dashboard/JobsTable'
import UploadPublishForm from '../components/upload/UploadPublishForm'
import PlatformConnections from '../components/platforms/PlatformConnections'
import { Toaster } from 'react-hot-toast'

type Tab = 'publish' | 'jobs' | 'connections'

const TABS: Array<{ id: Tab; label: string; icon: React.ReactNode }> = [
  { id: 'publish',     label: 'New Publish',   icon: <Film size={15} /> },
  { id: 'jobs',        label: 'Publish Jobs',  icon: <Clock size={15} /> },
  { id: 'connections', label: 'Connections',   icon: <Link2 size={15} /> },
]

export default function DashboardPage() {
  const { user, logout } = useAuthStore()
  const [tab, setTab] = useState<Tab>('publish')
  const [refreshKey, setRefreshKey] = useState(0)

  function handlePublished() {
    setRefreshKey(k => k + 1)
    setTab('jobs')
  }

  return (
    <div className="min-h-screen bg-forge-bg">
      <Toaster
        position="top-right"
        toastOptions={{
          style: {
            background: '#111118',
            color: '#e8e8f0',
            border: '1px solid #1e1e2e',
            fontFamily: '"DM Sans", sans-serif',
            fontSize: '14px',
          },
          success: { iconTheme: { primary: '#22c55e', secondary: '#111118' } },
          error:   { iconTheme: { primary: '#ef4444', secondary: '#111118' } },
        }}
      />

      {/* Top nav */}
      <header className="border-b border-forge-border bg-forge-surface/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-5xl mx-auto px-5 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-forge-accent flex items-center justify-center text-sm">🎬</div>
            <span className="font-display font-extrabold text-lg text-forge-text tracking-tight">ReelForge</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-forge-dim text-xs font-mono hidden sm:block">{user?.email}</span>
            <button
              onClick={() => setRefreshKey(k => k + 1)}
              className="text-forge-dim hover:text-forge-text transition-colors p-1.5"
              title="Refresh"
            >
              <RefreshCw size={15} />
            </button>
            <button
              onClick={logout}
              className="text-forge-dim hover:text-forge-text transition-colors p-1.5"
              title="Sign out"
            >
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-5 py-8 space-y-7">

        {/* Stats */}
        <StatsOverview refreshKey={refreshKey} />

        {/* Tab navigation */}
        <div className="flex items-center gap-1 border-b border-forge-border pb-0">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`
                flex items-center gap-2 px-4 py-2.5 text-sm font-display font-semibold
                border-b-2 -mb-px transition-all duration-150
                ${tab === t.id
                  ? 'border-forge-accent text-forge-accent'
                  : 'border-transparent text-forge-dim hover:text-forge-text'
                }
              `}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        <div className="animate-fade-in" key={tab}>
          {tab === 'publish' && (
            <div className="max-w-2xl">
              <div className="mb-6">
                <h2 className="font-display font-bold text-xl text-forge-text">Upload & Publish</h2>
                <p className="text-forge-dim text-sm mt-0.5">
                  Upload one video and publish to multiple platforms simultaneously.
                </p>
              </div>
              <UploadPublishForm onPublished={handlePublished} />
            </div>
          )}

          {tab === 'jobs' && (
            <div>
              <div className="mb-6">
                <h2 className="font-display font-bold text-xl text-forge-text">Publish Jobs</h2>
                <p className="text-forge-dim text-sm mt-0.5">
                  Track every publish attempt across all platforms.
                </p>
              </div>
              <JobsTable refreshKey={refreshKey} />
            </div>
          )}

          {tab === 'connections' && (
            <div>
              <div className="mb-6">
                <h2 className="font-display font-bold text-xl text-forge-text">Platform Connections</h2>
                <p className="text-forge-dim text-sm mt-0.5">
                  Connect your social accounts via OAuth to enable publishing.
                </p>
              </div>
              <PlatformConnections />
            </div>
          )}
        </div>
      </main>
    </div>
  )
}
