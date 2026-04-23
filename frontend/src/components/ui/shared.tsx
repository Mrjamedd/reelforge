import React from 'react'
import type { JobStatus, Platform } from '../../lib/api'

// ─── Status Badge ─────────────────────────────────────────────────────────────

const STATUS_CONFIG: Record<JobStatus, { label: string; color: string; dot: string }> = {
  queued:           { label: 'Queued',           color: 'text-forge-dim   bg-forge-muted/50 border-forge-muted',    dot: 'bg-forge-dim' },
  uploading:        { label: 'Uploading',         color: 'text-forge-blue  bg-forge-blue/10  border-forge-blue/30',  dot: 'bg-forge-blue animate-pulse-dot' },
  processing:       { label: 'Processing',        color: 'text-forge-yellow bg-forge-yellow/10 border-forge-yellow/30', dot: 'bg-forge-yellow animate-pulse-dot' },
  posted:           { label: 'Posted',            color: 'text-forge-green bg-forge-green/10 border-forge-green/30', dot: 'bg-forge-green' },
  failed:           { label: 'Failed',            color: 'text-forge-red   bg-forge-red/10   border-forge-red/30',   dot: 'bg-forge-red' },
  requires_manual:  { label: 'Manual Required',   color: 'text-forge-yellow bg-forge-yellow/10 border-forge-yellow/30', dot: 'bg-forge-yellow' },
  scheduled:        { label: 'Scheduled',         color: 'text-forge-accent bg-forge-accent/10 border-forge-accent/30', dot: 'bg-forge-accent' },
  cancelled:        { label: 'Cancelled',         color: 'text-forge-dim   bg-forge-muted/30 border-forge-muted/50', dot: 'bg-forge-dim' },
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const cfg = STATUS_CONFIG[status] ?? STATUS_CONFIG.queued
  return (
    <span className={`status-badge border ${cfg.color}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  )
}

// ─── Platform Badge ───────────────────────────────────────────────────────────

const PLATFORM_CONFIG: Record<Platform, { label: string; color: string; icon: string }> = {
  tiktok:    { label: 'TikTok',    color: 'text-white bg-[#010101] border-[#333]', icon: '🎵' },
  instagram: { label: 'Instagram', color: 'text-pink-300 bg-pink-950/40 border-pink-800/40', icon: '📸' },
  youtube:   { label: 'YouTube',   color: 'text-red-300 bg-red-950/40 border-red-800/40', icon: '▶' },
}

export function PlatformBadge({ platform }: { platform: Platform }) {
  const cfg = PLATFORM_CONFIG[platform]
  return (
    <span className={`status-badge border ${cfg.color}`}>
      <span className="text-[10px]">{cfg.icon}</span>
      {cfg.label}
    </span>
  )
}

// ─── Spinner ──────────────────────────────────────────────────────────────────

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size} height={size}
      viewBox="0 0 24 24" fill="none"
      className="animate-spin text-forge-accent"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" strokeOpacity="0.2" />
      <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

// ─── Empty State ──────────────────────────────────────────────────────────────

export function EmptyState({ icon, title, subtitle }: { icon: React.ReactNode; title: string; subtitle?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-3 text-center">
      <div className="text-forge-muted text-4xl mb-2">{icon}</div>
      <p className="font-display font-semibold text-forge-text">{title}</p>
      {subtitle && <p className="text-forge-dim text-sm max-w-xs">{subtitle}</p>}
    </div>
  )
}

// ─── Section Header ───────────────────────────────────────────────────────────

export function SectionHeader({ title, subtitle, action }: {
  title: string
  subtitle?: string
  action?: React.ReactNode
}) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h2 className="font-display font-bold text-xl text-forge-text">{title}</h2>
        {subtitle && <p className="text-forge-dim text-sm mt-0.5">{subtitle}</p>}
      </div>
      {action}
    </div>
  )
}

// ─── Format helpers ───────────────────────────────────────────────────────────

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

export function formatDuration(secs: number | null): string {
  if (!secs) return '—'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}
