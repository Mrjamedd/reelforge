import React, { useEffect, useState } from 'react'
import { RefreshCw, ExternalLink, ChevronDown, ChevronUp, RotateCcw, XCircle } from 'lucide-react'
import { jobsApi, type PublishJob, type AuditLog } from '../../lib/api'
import { StatusBadge, PlatformBadge, Spinner, EmptyState } from '../ui/shared'
import toast from 'react-hot-toast'
import { formatDistanceToNow } from 'date-fns'

function AuditTrail({ jobId }: { jobId: string }) {
  const [logs, setLogs] = useState<AuditLog[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    jobsApi.audit(jobId)
      .then(r => setLogs(r.data))
      .finally(() => setLoading(false))
  }, [jobId])

  if (loading) return <div className="py-4 flex justify-center"><Spinner size={16} /></div>

  return (
    <div className="mt-3 space-y-1.5 border-t border-forge-border pt-3">
      <p className="text-xs font-display font-semibold text-forge-dim uppercase tracking-wider mb-2">Audit Trail</p>
      {logs.map(log => (
        <div key={log.id} className="flex items-start gap-2 text-xs">
          <span className="text-forge-dim font-mono shrink-0 pt-0.5">
            {new Date(log.created_at).toLocaleTimeString()}
          </span>
          <div className="flex items-center gap-1.5 flex-wrap">
            {log.from_status && (
              <>
                <StatusBadge status={log.from_status} />
                <span className="text-forge-dim">→</span>
              </>
            )}
            <StatusBadge status={log.to_status} />
          </div>
          {log.message && (
            <span className="text-forge-dim leading-relaxed">{log.message}</span>
          )}
        </div>
      ))}
    </div>
  )
}

function JobRow({ job, onUpdate }: { job: PublishJob; onUpdate: (j: PublishJob) => void }) {
  const [expanded, setExpanded] = useState(false)
  const [retrying, setRetrying] = useState(false)
  const [cancelling, setCancelling] = useState(false)

  const canRetry = (job.status === 'failed' || job.status === 'requires_manual') && job.attempt_count < job.max_attempts
  const canCancel = job.status === 'queued' || job.status === 'scheduled'

  async function handleRetry() {
    setRetrying(true)
    try {
      const res = await jobsApi.retry(job.id)
      onUpdate(res.data)
      toast.success('Job re-queued')
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'Failed to retry')
    } finally {
      setRetrying(false)
    }
  }

  async function handleCancel() {
    setCancelling(true)
    try {
      const res = await jobsApi.cancel(job.id)
      onUpdate(res.data)
      toast.success('Job cancelled')
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'Failed to cancel')
    } finally {
      setCancelling(false)
    }
  }

  return (
    <div className="card hover:border-forge-muted transition-colors">
      <div className="flex items-start gap-3">
        {/* Platform + status */}
        <div className="flex flex-col gap-1.5 shrink-0 pt-0.5">
          <PlatformBadge platform={job.platform} />
          <StatusBadge status={job.status} />
        </div>

        {/* Meta */}
        <div className="flex-1 min-w-0">
          {job.title && (
            <p className="font-display font-semibold text-forge-text text-sm truncate">{job.title}</p>
          )}
          {job.caption && (
            <p className="text-forge-dim text-xs truncate mt-0.5">{job.caption}</p>
          )}
          <div className="flex items-center gap-3 mt-1.5 flex-wrap">
            <span className="text-forge-dim/70 text-xs font-mono">
              {formatDistanceToNow(new Date(job.updated_at), { addSuffix: true })}
            </span>
            {job.scheduled_for && (
              <span className="text-forge-accent text-xs font-mono flex items-center gap-1">
                🗓 {new Date(job.scheduled_for).toLocaleString()}
              </span>
            )}
            {job.attempt_count > 0 && (
              <span className="text-forge-dim/70 text-xs font-mono">
                {job.attempt_count}/{job.max_attempts} attempts
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          {job.platform_post_url && (
            <a
              href={job.platform_post_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-forge-dim hover:text-forge-accent transition-colors p-1"
              title="View post"
            >
              <ExternalLink size={14} />
            </a>
          )}
          {canRetry && (
            <button
              onClick={handleRetry}
              disabled={retrying}
              className="text-forge-dim hover:text-forge-green transition-colors p-1"
              title="Retry"
            >
              {retrying ? <Spinner size={14} /> : <RotateCcw size={14} />}
            </button>
          )}
          {canCancel && (
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="text-forge-dim hover:text-forge-red transition-colors p-1"
              title="Cancel"
            >
              {cancelling ? <Spinner size={14} /> : <XCircle size={14} />}
            </button>
          )}
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-forge-dim hover:text-forge-text transition-colors p-1"
          >
            {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {expanded && <AuditTrail jobId={job.id} />}
    </div>
  )
}

export default function JobsTable({ refreshKey }: { refreshKey: number }) {
  const [jobs, setJobs] = useState<PublishJob[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    jobsApi.list()
      .then(r => setJobs(r.data))
      .finally(() => setLoading(false))
  }, [refreshKey])

  function updateJob(updated: PublishJob) {
    setJobs(prev => prev.map(j => j.id === updated.id ? updated : j))
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40">
        <Spinner size={24} />
      </div>
    )
  }

  if (jobs.length === 0) {
    return (
      <EmptyState
        icon="📋"
        title="No publish jobs yet"
        subtitle="Upload a video and publish it to see jobs here."
      />
    )
  }

  return (
    <div className="space-y-2">
      {jobs.map(job => (
        <JobRow key={job.id} job={job} onUpdate={updateJob} />
      ))}
    </div>
  )
}
