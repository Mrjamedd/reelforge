import React, { useCallback, useState } from 'react'
import { useDropzone } from 'react-dropzone'
import { Upload, X, Film, Clock, Eye, Globe, Lock, Users } from 'lucide-react'
import { uploadsApi, jobsApi, oauthApi, type Upload as UploadType, type Platform, type PlatformStatus, type PrivacyLevel } from '../../lib/api'
import { Spinner, formatBytes, formatDuration } from '../ui/shared'
import toast from 'react-hot-toast'

const PLATFORM_META: Record<Platform, { name: string; icon: string }> = {
  tiktok:    { name: 'TikTok',    icon: '🎵' },
  instagram: { name: 'Instagram', icon: '📸' },
  youtube:   { name: 'YouTube',   icon: '▶' },
}

const PRIVACY_OPTIONS: Array<{ value: PrivacyLevel; label: string; icon: React.ReactNode }> = [
  { value: 'public',   label: 'Public',   icon: <Globe size={12} /> },
  { value: 'private',  label: 'Private',  icon: <Lock size={12} /> },
  { value: 'unlisted', label: 'Unlisted', icon: <Eye size={12} /> },
  { value: 'friends',  label: 'Friends',  icon: <Users size={12} /> },
]

interface PlatformMeta {
  enabled: boolean
  title: string
  caption: string
  hashtags: string
  privacy: PrivacyLevel
  scheduled_for: string
  schedule: boolean
}

function defaultMeta(): PlatformMeta {
  return { enabled: false, title: '', caption: '', hashtags: '', privacy: 'public', scheduled_for: '', schedule: false }
}

export default function UploadPublishForm({ onPublished }: { onPublished: () => void }) {
  const [upload, setUpload] = useState<UploadType | null>(null)
  const [uploading, setUploading] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [platforms, setPlatforms] = useState<Record<Platform, PlatformMeta>>({
    tiktok: defaultMeta(),
    instagram: defaultMeta(),
    youtube: defaultMeta(),
  })
  const [connectedPlatforms, setConnectedPlatforms] = useState<Platform[]>([])
  const [statuses, setStatuses] = useState<PlatformStatus[]>([])
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)

  // Load platform connection statuses on mount
  React.useEffect(() => {
    oauthApi.statuses().then(r => {
      setStatuses(r.data)
      setConnectedPlatforms(r.data.filter(s => s.connected).map(s => s.platform))
    })
  }, [])

  const onDrop = useCallback(async (acceptedFiles: File[]) => {
    const file = acceptedFiles[0]
    if (!file) return
    setPreviewUrl(URL.createObjectURL(file))
    setUploading(true)
    try {
      const res = await uploadsApi.upload(file)
      setUpload(res.data)
      toast.success('Video uploaded successfully')
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'Upload failed')
      setPreviewUrl(null)
    } finally {
      setUploading(false)
    }
  }, [])

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: { 'video/*': ['.mp4', '.mov', '.avi', '.webm', '.mkv'] },
    maxFiles: 1,
    disabled: uploading || !!upload,
  })

  function togglePlatform(p: Platform) {
    setPlatforms(prev => ({
      ...prev,
      [p]: { ...prev[p], enabled: !prev[p].enabled },
    }))
  }

  function updateMeta(p: Platform, key: keyof PlatformMeta, value: any) {
    setPlatforms(prev => ({ ...prev, [p]: { ...prev[p], [key]: value } }))
  }

  function copyToAll(source: Platform) {
    const src = platforms[source]
    setPlatforms(prev => {
      const next = { ...prev }
      ;(['tiktok', 'instagram', 'youtube'] as Platform[]).forEach(p => {
        if (p !== source && prev[p].enabled) {
          next[p] = { ...prev[p], title: src.title, caption: src.caption, hashtags: src.hashtags }
        }
      })
      return next
    })
    toast.success('Caption copied to all enabled platforms')
  }

  async function handlePublish() {
    if (!upload) return
    const enabledPlatforms = (Object.keys(platforms) as Platform[]).filter(p => platforms[p].enabled)
    if (enabledPlatforms.length === 0) {
      toast.error('Enable at least one platform to publish to')
      return
    }

    setPublishing(true)
    try {
      const jobs = enabledPlatforms.map(p => {
        const meta = platforms[p]
        return {
          platform: p,
          title: meta.title || undefined,
          caption: meta.caption || undefined,
          hashtags: meta.hashtags || undefined,
          privacy: meta.privacy,
          scheduled_for: meta.schedule && meta.scheduled_for
            ? new Date(meta.scheduled_for).toISOString()
            : undefined,
        }
      })
      await jobsApi.bulkCreate(upload.id, jobs)
      toast.success(`${jobs.length} publish job${jobs.length > 1 ? 's' : ''} queued!`)
      onPublished()
      // Reset form
      setUpload(null)
      setPreviewUrl(null)
      setPlatforms({ tiktok: defaultMeta(), instagram: defaultMeta(), youtube: defaultMeta() })
    } catch (e: any) {
      toast.error(e.response?.data?.detail || 'Failed to queue publish jobs')
    } finally {
      setPublishing(false)
    }
  }

  const enabledCount = (Object.keys(platforms) as Platform[]).filter(p => platforms[p].enabled).length

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Drop Zone */}
      {!upload ? (
        <div
          {...getRootProps()}
          className={`
            border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-all duration-200
            ${isDragActive
              ? 'border-forge-accent bg-forge-accent/5'
              : 'border-forge-border hover:border-forge-muted hover:bg-forge-surface/50'
            }
            ${uploading ? 'pointer-events-none' : ''}
          `}
        >
          <input {...getInputProps()} />
          <div className="flex flex-col items-center gap-3">
            {uploading ? (
              <>
                <Spinner size={32} />
                <p className="font-display font-semibold text-forge-text">Uploading…</p>
              </>
            ) : (
              <>
                <div className="w-12 h-12 rounded-full bg-forge-accent/10 flex items-center justify-center">
                  <Upload size={22} className="text-forge-accent" />
                </div>
                <div>
                  <p className="font-display font-semibold text-forge-text">
                    {isDragActive ? 'Drop it here' : 'Drop your video here'}
                  </p>
                  <p className="text-forge-dim text-sm mt-1">MP4, MOV, AVI, WebM · Max 4 GB</p>
                  <p className="text-forge-dim/60 text-xs mt-1">Vertical 9:16 recommended for Shorts/Reels</p>
                </div>
                <button className="btn-ghost mt-2">Browse files</button>
              </>
            )}
          </div>
        </div>
      ) : (
        // Upload preview
        <div className="card flex gap-4 items-start animate-slide-up">
          <div className="relative w-16 shrink-0">
            {previewUrl ? (
              <video
                src={previewUrl}
                className="w-full aspect-[9/16] object-cover rounded-lg bg-forge-bg"
                muted
                playsInline
                onMouseOver={e => (e.currentTarget as HTMLVideoElement).play()}
                onMouseOut={e => { (e.currentTarget as HTMLVideoElement).pause(); (e.currentTarget as HTMLVideoElement).currentTime = 0 }}
              />
            ) : (
              <div className="w-full aspect-[9/16] bg-forge-muted rounded-lg flex items-center justify-center">
                <Film size={20} className="text-forge-dim" />
              </div>
            )}
          </div>
          <div className="flex-1 min-w-0">
            <p className="font-display font-semibold text-forge-text truncate text-sm">{upload.original_filename}</p>
            <div className="flex items-center gap-3 mt-1.5 flex-wrap">
              <span className="text-forge-dim text-xs font-mono">{formatBytes(upload.file_size_bytes)}</span>
              {upload.duration_seconds && (
                <span className="text-forge-dim text-xs font-mono">{formatDuration(upload.duration_seconds)}</span>
              )}
              {upload.width && upload.height && (
                <span className={`text-xs font-mono ${upload.width < upload.height ? 'text-forge-green' : 'text-forge-yellow'}`}>
                  {upload.width}×{upload.height}
                  {upload.width < upload.height ? ' ✓ vertical' : ' ⚠ landscape'}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => { setUpload(null); setPreviewUrl(null) }}
            className="text-forge-dim hover:text-forge-text transition-colors p-1"
          >
            <X size={16} />
          </button>
        </div>
      )}

      {/* Platform toggles + metadata */}
      {upload && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-display font-bold text-forge-text text-sm uppercase tracking-wider">Publish To</h3>
            {enabledCount > 1 && (
              <button
                onClick={() => copyToAll(
                  (Object.keys(platforms) as Platform[]).find(p => platforms[p].enabled) as Platform
                )}
                className="text-xs text-forge-accent hover:text-forge-accentHover transition-colors font-mono"
              >
                Copy caption to all
              </button>
            )}
          </div>

          {(Object.keys(platforms) as Platform[]).map(p => {
            const meta = platforms[p]
            const platformStatus = statuses.find(s => s.platform === p)
            const isConnected = platformStatus?.connected ?? false
            const pmeta = PLATFORM_META[p]

            return (
              <div
                key={p}
                className={`card transition-all duration-200 ${meta.enabled ? 'border-forge-accent/30' : ''}`}
              >
                {/* Platform toggle row */}
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => isConnected && togglePlatform(p)}
                    disabled={!isConnected}
                    className={`
                      relative w-10 h-5 rounded-full transition-all duration-200 shrink-0
                      ${meta.enabled ? 'bg-forge-accent' : 'bg-forge-muted'}
                      ${!isConnected ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}
                    `}
                  >
                    <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all duration-200 ${meta.enabled ? 'left-5' : 'left-0.5'}`} />
                  </button>
                  <span className="text-lg">{pmeta.icon}</span>
                  <span className="font-display font-semibold text-sm text-forge-text">{pmeta.name}</span>
                  {!isConnected && (
                    <span className="text-xs text-forge-dim font-mono ml-auto">Not connected</span>
                  )}
                </div>

                {/* Expanded metadata form */}
                {meta.enabled && (
                  <div className="mt-4 space-y-3 animate-slide-up">
                    {/* Title (YouTube mostly) */}
                    {p === 'youtube' && (
                      <div>
                        <label className="label">Title</label>
                        <input
                          className="input"
                          placeholder="Video title (required for YouTube)"
                          value={meta.title}
                          onChange={e => updateMeta(p, 'title', e.target.value)}
                          maxLength={100}
                        />
                        <p className="text-right text-forge-dim text-xs mt-1">{meta.title.length}/100</p>
                      </div>
                    )}

                    {/* Caption */}
                    <div>
                      <label className="label">Caption / Description</label>
                      <textarea
                        className="input resize-none h-20"
                        placeholder={p === 'youtube' ? 'Description…' : 'Write a caption…'}
                        value={meta.caption}
                        onChange={e => updateMeta(p, 'caption', e.target.value)}
                        maxLength={p === 'youtube' ? 5000 : 2200}
                      />
                      <p className="text-right text-forge-dim text-xs mt-1">
                        {meta.caption.length}/{p === 'youtube' ? 5000 : 2200}
                      </p>
                    </div>

                    {/* Hashtags */}
                    <div>
                      <label className="label">Hashtags</label>
                      <input
                        className="input font-mono text-xs"
                        placeholder="fyp, trending, shorts  (comma-separated, # optional)"
                        value={meta.hashtags}
                        onChange={e => updateMeta(p, 'hashtags', e.target.value)}
                      />
                    </div>

                    {/* Privacy + Schedule row */}
                    <div className="flex flex-wrap gap-3">
                      <div className="flex-1 min-w-[140px]">
                        <label className="label">Privacy</label>
                        <div className="flex gap-1 flex-wrap">
                          {PRIVACY_OPTIONS
                            .filter(o => p !== 'youtube' ? o.value !== 'unlisted' : true)
                            .filter(o => p !== 'instagram' ? o.value !== 'friends' : true)
                            .map(o => (
                              <button
                                key={o.value}
                                onClick={() => updateMeta(p, 'privacy', o.value)}
                                className={`
                                  flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-mono transition-all
                                  ${meta.privacy === o.value
                                    ? 'bg-forge-accent text-white'
                                    : 'bg-forge-muted text-forge-dim hover:text-forge-text'
                                  }
                                `}
                              >
                                {o.icon} {o.label}
                              </button>
                            ))
                          }
                        </div>
                      </div>

                      {/* Schedule toggle */}
                      <div className="flex-1 min-w-[200px]">
                        <label className="label">When to publish</label>
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => updateMeta(p, 'schedule', !meta.schedule)}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-mono transition-all
                              ${meta.schedule
                                ? 'bg-forge-accent/20 text-forge-accent border border-forge-accent/30'
                                : 'bg-forge-muted text-forge-dim hover:text-forge-text'
                              }`}
                          >
                            <Clock size={12} />
                            {meta.schedule ? 'Scheduled' : 'Publish now'}
                          </button>
                          {meta.schedule && (
                            <input
                              type="datetime-local"
                              className="input text-xs py-1.5 flex-1"
                              value={meta.scheduled_for}
                              onChange={e => updateMeta(p, 'scheduled_for', e.target.value)}
                              min={new Date().toISOString().slice(0, 16)}
                            />
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )
          })}

          {/* Publish button */}
          <div className="pt-2">
            <button
              onClick={handlePublish}
              disabled={publishing || enabledCount === 0}
              className="btn-primary w-full py-3 text-base flex items-center justify-center gap-2"
            >
              {publishing ? (
                <><Spinner size={16} /> Queuing jobs…</>
              ) : (
                <>🚀 Publish to {enabledCount} platform{enabledCount !== 1 ? 's' : ''}</>
              )}
            </button>
            {enabledCount === 0 && (
              <p className="text-center text-forge-dim text-xs mt-2">Enable at least one platform above</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
